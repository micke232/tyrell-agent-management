# Exercise the actual Homebrew installer in an isolated prefix; no tap or app is installed globally.
require "formula"
require "tmpdir"
require "fileutils"
load ARGV.fetch(0)
Dir.mktmpdir("agent-hub-brew-") do |directory|
  root = Pathname.new(directory)
  source = root/"source"
  source.mkpath
  system "/usr/bin/tar", "-xzf", ARGV.fetch(1), "--strip-components=1", "-C", source.to_s
  formula = AgentHub.new("agent-hub", Pathname.new(ARGV.fetch(0)), :stable)
  formula.define_singleton_method(:prefix) { root/"prefix" }
  formula.define_singleton_method(:logs) { root/"logs" }
  formula.instance_variable_set(:@buildpath, source)
  ENV.extend(Stdenv)
  formula.install
  executable = formula.bin/"tyrell"
  abort "Missing linked command" unless executable.exist?
  version = IO.popen([executable.to_s, "--version"], &:read)
  abort "Wrong version" unless version.include?(formula.version.to_s)
  help = IO.popen([executable.to_s, "--help"], &:read)
  abort "Missing CLI" unless help.include?("Tyrell Agent Management")
  puts "PASS: actual Homebrew virtualenv installer and linked command, isolated temporary prefix"
end
