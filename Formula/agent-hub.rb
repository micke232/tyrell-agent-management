class AgentHub < Formula
  include Language::Python::Virtualenv

  desc "Terminal hub for independent Codex and GitHub Copilot agents"
  homepage "https://github.com/micke232/tyrell-agent-management"
  url "https://github.com/micke232/tyrell-agent-management/releases/download/v0.2.0a6/agent-hub-0.2.0a6-homebrew.tar.gz"
  version "0.2.0a6"
  sha256 "9edf58765591336eb8ef8042123661a8380debbcdb3bbd8267c2c9f7530cc5c1"

  depends_on "python@3.14"
  depends_on "git"

  def install
    venv = virtualenv_create(libexec, Formula["python@3.14"].opt_bin/"python3.14")
    venv.pip_install_and_link buildpath/"agent_hub_management-0.2.0a6-py3-none-any.whl"
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
