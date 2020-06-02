import requests
import re
from html.parser import HTMLParser
from urllib.request import urlretrieve
import tempfile
import shutil
import subprocess
import sys


class VersionIndexHTMLParser(HTMLParser):
    def __init__(self, *, convert_charrefs=True):
        super().__init__(convert_charrefs=convert_charrefs)
        self.versions = []
        self.ver_regexp = re.compile('^v\d+\.\d+(\.\d+)?/$')

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

        self.arch_start_regexp = re.compile(".*Build for (.+) succeeded.*")
        self.file_name_regexp = re.compile(
            "^linux.*{}.*((_all\.deb)|(generic.*_{}\.deb))".format(version, arch))
        self.arch_found = False
        self.correct_arch_found = False

    def handle_data(self, data):
        arch_matched = self.arch_start_regexp.match(data)
        if arch_matched is not None:
            self.arch_found = True
            if arch_matched.group(1) == self.arch:
                self.correct_arch_found = True
            else:
                self.correct_arch_found = False
        if self.arch_found and self.correct_arch_found:
            if self.file_name_regexp.match(data):
                self.files.append(data)


arch = "amd64"
flavor = 'generic'
index = requests.get('https://kernel.ubuntu.com/~kernel-ppa/mainline/')
index_parser = VersionIndexHTMLParser()
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

last_ver_page = requests.get(
    'https://kernel.ubuntu.com/~kernel-ppa/mainline/v{}/'.format(last_ver))
last_ver_parser = SingleVerHTMLParser(arch, flavor, last_ver)
last_ver_parser.feed(str(last_ver_page.content))

dirpath = tempfile.mkdtemp()
for file in last_ver_parser.files:
    dst_file = filename = "{}/{}".format(dirpath, file)
    print("Fetching: ", dst_file)
    urlretrieve("https://kernel.ubuntu.com/~kernel-ppa/mainline/v{}/{}".format(
        last_ver, file), dst_file)
installation_order = ["linux-headers", "linux-modules", "linux-image"]
for file_prefix in installation_order:
    files = [f for f in last_ver_parser.files if f.startswith(file_prefix)]
    for file in files:
        dst_file = filename = "{}/{}".format(dirpath, file)
        print("Installing: ", dst_file)
        run_res = subprocess.run(["sudo", "dpkg", "-i", dst_file])
shutil.rmtree(dirpath)

for file in last_ver_parser.files:
    if file.startswith("linux-image"):
        image_name = "/boot/vmlinuz-" + \
            "-".join(file.split("_")[0].split("-")[3:])
        print("Singing kernel image: " + image_name)
        subprocess.run(["sudo", "sbsign", "--key", "/root/mok/MOK.priv", "--cert",
                        "/root/mok/MOK.pem", image_name, "--output", image_name])
