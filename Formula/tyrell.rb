class Tyrell < Formula
  include Language::Python::Virtualenv

  desc "Terminal hub for independent Codex and GitHub Copilot agents"
  homepage "https://github.com/micke232/tyrell-agent-management"
  url "https://github.com/micke232/tyrell-agent-management/releases/download/v0.2.0a8/tyrell-0.2.0a8-homebrew.tar.gz"
  version "0.2.0a8"
  sha256 "d006a7978a8103f3bf079d8126e871c1b959fe4ddafaa2b381eb465220103da6"

  depends_on "python@3.14"
  depends_on "git"

  def install
    venv = virtualenv_create(libexec, Formula["python@3.14"].opt_bin/"python3.14")
    venv.pip_install_and_link buildpath/"tyrell_agent_management-0.2.0a8-py3-none-any.whl"
  end

  def caveats
    <<~EOS
      Run tyrell to choose your terminal and configure your connections.
      Ghostty installation is optional and requires your explicit choice.
      Install and sign in to Codex CLI or GitHub Copilot CLI separately.
      F10 Settings shows connection and setup information.
    EOS
  end

  test do
    assert_match "Tyrell Agent Management #{version}", shell_output("#{bin}/tyrell --version")
    assert_match "Tyrell Agent Management", shell_output("#{bin}/tyrell --help")
  end
end
