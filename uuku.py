import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from urllib.request import urlretrieve

import requests


class VersionIndexHTMLParser(HTMLParser):
    def __init__(self, prefix, *, convert_charrefs=True):
        super().__init__(convert_charrefs=convert_charrefs)
        self.versions = []
        if prefix is None:
            self.ver_regexp = re.compile('^v\d+\.\d+(\.\d+)?/$')
        else:
            matched = re.match('^(\d+)(\.\d+)?$', prefix)
            if not matched:
                print(f"Version prefix must match pattern 'X.Y' or just 'X', but '{prefix}' was found.")
                sys.exit(1)
            groups = matched.groups()
            if groups[1] is None:
                self.ver_regexp = re.compile(f'^v{groups[0]}\.\d+(\.\d+)?/$')
            else:
                self.ver_regexp = re.compile(f'^v{groups[0]}\.{groups[1][1:]}(\.\d+)?/$')

    def handle_data(self, data):
        if self.ver_regexp.match(data):
            self.versions.append(data[1:-1])


class SingleVerHTMLParser(HTMLParser):
    def __init__(self, arch, flavor, version, *, convert_charrefs=True):
        super().__init__(convert_charrefs=convert_charrefs)
        self.arch = arch
        self.flavor = flavor
        self.version = version
        self.files = []

        self.arch_start_regexp = re.compile(".*(Build for|Test) (\w+)(\/build)? succeeded.*")
        self.file_name_regexp = re.compile(f"({arch}\/)?linux.*{version}.*((_all\.deb)|(generic.*_{arch}\.deb))")
        self.arch_found = False
        self.correct_arch_found = False

    def handle_data(self, data):
        arch_matched = self.arch_start_regexp.match(data)
        if arch_matched is not None:
            self.arch_found = True
            if arch_matched.group(2) == self.arch:
                self.correct_arch_found = True
            else:
                self.correct_arch_found = False
        if self.arch_found and self.correct_arch_found:
            if self.file_name_regexp.match(data):
                self.files.append(data)


parser = argparse.ArgumentParser(description="Install upstream ubuntu kernel")
parser.add_argument("-p", "--prefix", type=str, help="install only from release versions matching the prefix")
parser.add_argument("-a", "--arch", type=str, help="use kernel for specific architecture", default="amd64")
parser.add_argument("-f", "--flavor", type=str, help="use kernel for of specific flavor (default: generic)",
                    default="generic", choices=["generic", "lowlatency"])
parser.add_argument("-s", "--sign", type=bool, help="should the kernel be signed after installing (default: True)",
                    default=True, choices=[True, False])
args = parser.parse_args()

index = requests.get('https://kernel.ubuntu.com/~kernel-ppa/mainline/')
index_parser = VersionIndexHTMLParser(args.prefix)
index_parser.feed(str(index.content, 'utf-8'))
sorted_vers = sorted(
    index_parser.versions, key=lambda s: list(map(int, s.split('.'))))
last_ver = sorted_vers[-1]
if len(last_ver.split(".")) == 2:
    last_ver += ".0"
print("Last kernel version found: ", last_ver)

run_res = subprocess.run(["uname", "-r"], capture_output=True)
local_ver = str(run_res.stdout, 'utf-8').split("-")[0]
print("Local kernel version is: ", local_ver)
if last_ver == local_ver:
    "You're already on the latest version, nothing to do"
    sys.exit(0)

last_ver_page = requests.get( f"https://kernel.ubuntu.com/~kernel-ppa/mainline/v{last_ver}/")
last_ver_parser = SingleVerHTMLParser(args.arch, args.flavor, last_ver)
last_ver_parser.feed(str(last_ver_page.content))

dirpath = tempfile.mkdtemp()
downloaded = []
for file in last_ver_parser.files:
    filename = file[len(args.arch) + 1:] if file.startswith(args.arch) else file
    dst_file = f"{dirpath}/{filename}"
    print("Fetching: ", dst_file)
    urlretrieve(f"https://kernel.ubuntu.com/~kernel-ppa/mainline/v{last_ver}/{file}", dst_file)
    downloaded.append(filename)
installation_order = ["linux-headers-.*_all", f"linux-headers-.*-{args.flavor}", f"linux-modules.*-{args.flavor}",
                      f"linux-image.*-{args.flavor}"]
for file_prefix_regexp in [re.compile(x) for x in installation_order]:
    files = [f for f in downloaded if file_prefix_regexp.match(f)]
    for file in files:
        dst_file = f"{dirpath}/{file}"
        print("Installing: ", dst_file)
        run_res = subprocess.run(["sudo", "dpkg", "-i", dst_file])
shutil.rmtree(dirpath)

if args.sign:
    for file in downloaded:
        if file.startswith("linux-image"):
            image_name = "/boot/vmlinuz-" + \
                         "-".join(file.split("_")[0].split("-")[3:])
            print("Singing kernel image: " + image_name)
            subprocess.run(["sudo", "sbsign", "--key", "/root/mok/MOK.priv", "--cert",
                            "/root/mok/MOK.pem", image_name, "--output", image_name])
