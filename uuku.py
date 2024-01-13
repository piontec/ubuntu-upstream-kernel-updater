import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from urllib import request
from urllib.request import urlretrieve


release_key = "60AA 7B6F 3043 4AE6 8E56  9963 E50C 6A09 17C6 22B0"
ver = "0.1.4"


class VersionIndexHTMLParser(HTMLParser):
    def __init__(self, prefix, *, convert_charrefs=True):
        super().__init__(convert_charrefs=convert_charrefs)
        self.versions = []
        if prefix is None:
            self.ver_regexp = re.compile(r"^v\d+\.\d+(\.\d+)?/$")
        else:
            matched = re.match(r"^(\d+)(\.\d+)?$", prefix)
            if not matched:
                print(
                    f"Version prefix must match pattern 'X.Y' or just 'X', but '{prefix}' was found."
                )
                sys.exit(1)
            groups = matched.groups()
            if groups[1] is None:
                self.ver_regexp = re.compile(f"^v{groups[0]}" + r"\.\d+(\.\d+)?/$")
            else:
                self.ver_regexp = re.compile(
                    f"^v{groups[0]}" + r"\." + groups[1][1:] + r"(\.\d+)?/$"
                )

    def handle_data(self, data):
        if self.ver_regexp.match(data):
            self.versions.append(data[1:-1])

    def error(self, message):
        print(f"Error parsing index page: {message}")
        sys.exit(2)


class SingleVerHTMLParser(HTMLParser):
    def __init__(self, arch, flavor, version, *, convert_charrefs=True):
        super().__init__(convert_charrefs=convert_charrefs)
        self.files = []

        self.file_name_regexp = re.compile(
            r"linux.*"
            + version
            + r".*((_all\.deb)|("
            + flavor
            + ".*_"
            + arch
            + r"\.deb))"
        )

    def handle_data(self, data):
        if self.file_name_regexp.match(data):
            self.files.append(data)

    def error(self, message):
        print(f"Error parsing release page: {message}")
        sys.exit(2)


parser = argparse.ArgumentParser(description=f"Ubuntu Upstream Kernel Updater v{ver}")
parser.add_argument(
    "-p",
    "--prefix",
    type=str,
    help="Install only from release versions matching the prefix.",
)
parser.add_argument(
    "-a",
    "--arch",
    type=str,
    help="Use kernel for specific architecture (default: amd64).",
    default="amd64",
)
parser.add_argument(
    "-f",
    "--flavor",
    type=str,
    help="Use kernel of specific flavor (default: generic)",
    default="generic",
    choices=["generic", "lowlatency"],
)
parser.add_argument(
    "-s",
    "--sign",
    type=str,
    help="Sign the kernel image after installing. This needs to be a path"
    + " to a directory with 'MOK.priv' and 'MOK.pem' files. 'sudo' and"
    + "'sbsign' must be installed in the system",
)
args = parser.parse_args()

with request.urlopen("https://kernel.ubuntu.com/~kernel-ppa/mainline/") as response:
    index = response.read()
index_parser = VersionIndexHTMLParser(args.prefix)
index_parser.feed(str(index, "utf-8"))
sorted_vars = sorted(index_parser.versions, key=lambda s: list(map(int, s.split("."))))
last_ver = sorted_vars[-1]
dir_ver = last_ver
if len(last_ver.split(".")) == 2:
    last_ver += ".0"
print("Last kernel version found: ", last_ver)

run_res = subprocess.run(["uname", "-r"], capture_output=True)
local_ver = str(run_res.stdout, "utf-8").split("-")[0]
print("Local kernel version is: ", local_ver)
if last_ver == local_ver:
    print("You're already running the latest version, nothing to do.")
    sys.exit(0)

run_res = subprocess.run(
    ["dpkg", "--no-pager", "-l", f"linux-image-unsigned-{last_ver}*"],
    capture_output=True,
)
if run_res.returncode == 0:
    print("You already have the latest version installed, nothing to do.")
    sys.exit(0)

with request.urlopen(
    f"https://kernel.ubuntu.com/~kernel-ppa/mainline/v{dir_ver}/{args.arch}/"
) as response:
    last_ver_page = response.read()
last_ver_parser = SingleVerHTMLParser(args.arch, args.flavor, last_ver)
last_ver_parser.feed(str(last_ver_page, "utf-8"))

dirpath = tempfile.mkdtemp()
downloaded = []
for file in last_ver_parser.files + ["CHECKSUMS", "CHECKSUMS.gpg"]:
    filename = file[len(args.arch) + 1 :] if file.startswith(args.arch) else file
    dst_file = f"{dirpath}/{filename}"
    print("Fetching: ", dst_file)
    urlretrieve(
        f"https://kernel.ubuntu.com/~kernel-ppa/mainline/v{dir_ver}/{args.arch}/{file}",
        dst_file,
    )
    downloaded.append(filename)

# validate
print("Validating downloaded files")
run_res = subprocess.run(
    ["shasum", "-c", "CHECKSUMS"], capture_output=True, cwd=dirpath
)
check_results = str(run_res.stdout, "utf-8").splitlines()
for file in last_ver_parser.files:
    if file + ": OK" not in check_results:
        print(f"Checksum validation failed for file: {file}")
        sys.exit(3)
run_res = subprocess.run([f"gpg -k | grep '{release_key}'"], shell=True)
if run_res.returncode != 0:
    print("Fetching GPG release key")
    subprocess.run(
        ["gpg", "--keyserver", "pool.sks-keyservers.net", "--recv-key", release_key]
    )
print("Validating CHECKSUM file signature")
subprocess.run(
    ["gpg", "--verify", "CHECKSUMS.gpg", "CHECKSUMS"], cwd=dirpath, check=True
)

# install
installation_order = [
    "linux-headers-.*_all",
    f"linux-headers-.*-{args.flavor}",
    f"linux-modules.*-{args.flavor}",
    f"linux-image.*-{args.flavor}",
]
for file_prefix_regexp in [re.compile(x) for x in installation_order]:
    files = [f for f in downloaded if file_prefix_regexp.match(f)]
    for file in files:
        dst_file = f"{dirpath}/{file}"
        print("Installing: ", dst_file)
        run_res = subprocess.run(["sudo", "dpkg", "-i", dst_file])
shutil.rmtree(dirpath)

if args.sign is not None and args.sign != "":
    for file in downloaded:
        if file.startswith("linux-image"):
            image_name = "/boot/vmlinuz-" + "-".join(file.split("_")[0].split("-")[3:])
            print("Singing kernel image: " + image_name)
            subprocess.run(
                [
                    "sudo",
                    "sbsign",
                    "--key",
                    f"{args.sign}/MOK.priv",
                    "--cert",
                    f"{args.sign}/MOK.pem",
                    image_name,
                    "--output",
                    image_name,
                ],
                check=True,
            )
subprocess.run(["sudo", "apt-get", "install", "-f"])
