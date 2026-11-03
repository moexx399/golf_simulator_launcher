"""Write the Homebrew formula for a release.

The release workflow (.github/workflows/build-exe.yml) runs this after publishing a
release, then pushes the result to the tap repository, so `brew upgrade` picks up
each new version. It can also be run by hand:

    python packaging/homebrew/formula.py --version 1.2.3 --tag v1.2.3 --dist DIR

DIR holds the release's archives; their SHA-256 checksums go into the formula.
Standard library only.
"""
import argparse
import hashlib
import os

REPO = "moexx399/golf_simulator_launcher"
# Homebrew platform -> release archive
ARCHIVES = {
    "macos_arm": "golf_simulator_launcher-macos-arm64.tar.gz",
    "macos_intel": "golf_simulator_launcher-macos-x64.tar.gz",
    "linux_intel": "golf_simulator_launcher-linux-x64.tar.gz",
}

TEMPLATE = '''# Generated for each release by packaging/homebrew/formula.py in {repo}: edits are overwritten.
class GolfSimulatorLauncher < Formula
  desc "Full-screen, touch-friendly menu for booting golf simulators"
  homepage "https://github.com/{repo}"
  version "{version}"
  license "MIT"

  depends_on "tesseract"

  on_macos do
    on_arm do
      url "{url}/{macos_arm}"
      sha256 "{sha_macos_arm}"
    end
    on_intel do
      url "{url}/{macos_intel}"
      sha256 "{sha_macos_intel}"
    end
    depends_on "cliclick"
  end

  on_linux do
    on_intel do
      url "{url}/{linux_intel}"
      sha256 "{sha_linux_intel}"
    end
    depends_on "imagemagick"
    depends_on "xdotool"
    depends_on "xwininfo"
  end

  # A prebuilt bundle: its files are all where it expects them.
  skip_clean "libexec"

  def install
    libexec.install Dir["*"]
    bin.write_exec_script libexec/"golf_simulator_launcher"
  end

  def caveats
    <<~EOS
      Your launcher.json and launcher.log are kept in your user data folder:
        #{{OS.mac? ? "~/Library/Application Support/golf_simulator_launcher" : "~/.local/share/golf_simulator_launcher"}}
      The simulators in the sample config are Windows programs and need Wine or similar.
      Try it without starting anything: golf_simulator_launcher --windowed --dry-run
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/golf_simulator_launcher --version")
  end
end
'''


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def formula(version, tag, shas, repo=REPO):
    """The formula's text. shas: {"macos_arm"|"macos_intel"|"linux_intel": sha256 hex}."""
    missing = set(ARCHIVES) - set(shas)
    if missing:
        raise ValueError("no checksum for " + ", ".join(sorted(missing)))
    return TEMPLATE.format(repo=repo, version=version.lstrip("v"),
                           url=f"https://github.com/{repo}/releases/download/{tag}",
                           **ARCHIVES, **{f"sha_{key}": value for key, value in shas.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="release version, e.g. 1.2.3")
    parser.add_argument("--tag", required=True, help="the release's git tag, e.g. v1.2.3")
    parser.add_argument("--dist", required=True, help="folder with the release's archives")
    parser.add_argument("--repo", default=REPO, help=f"GitHub repository (default {REPO})")
    parser.add_argument("--out", default="golf_simulator_launcher.rb", help="where to write the formula")
    args = parser.parse_args()
    shas = {}
    for key, name in ARCHIVES.items():
        path = os.path.join(args.dist, name)
        if not os.path.exists(path):
            raise SystemExit(f"missing {path}")
        shas[key] = sha256(path)
    with open(args.out, "w", encoding="utf-8", newline="\n") as out:
        out.write(formula(args.version, args.tag, shas, args.repo))
    print(f"wrote {args.out} for {args.version}")


if __name__ == "__main__":
    main()
