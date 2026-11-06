"""Exercise the GUI's actual destination list without instantiating a WinForms window."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def target_probe(tmp_path_factory):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip(".NET SDK is required for the GUI destination unit")
    folder = tmp_path_factory.mktemp("gui-destination-unit")
    backend = Path(__file__).resolve().parents[1] / "gui/UpdateBackend.cs"
    (folder / "Probe.csproj").write_text('''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>
<ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup>
<ItemGroup><Compile Include="%s" Link="UpdateBackend.cs" /></ItemGroup></Project>'''
                                        % backend.as_posix(), encoding="utf-8")
    (folder / "Program.cs").write_text('''using System.Reflection;
using System.Text.Json;
using Kiln;
var method = typeof(UpdateBackend).GetMethod("PreparationTargets");
if (method is null) { Console.Error.WriteLine("GUI has no shared actual-destination preparation"); return 1; }
var value = method.Invoke(null, new object?[] { args[0], args.Length > 1 ? args[1] : null });
Console.WriteLine(JsonSerializer.Serialize(value));
return 0;
namespace Kiln {
  public sealed class KilnInstall { public string Python { get; init; } = ""; public string Root { get; init; } = ""; }
  public static class JsonProbe { public static string? Str(this JsonElement e, string name) => e.GetProperty(name).GetString(); }
}''', encoding="utf-8")
    built = subprocess.run([dotnet, "build", str(folder / "Probe.csproj"), "-nologo", "-v:quiet"],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    return dotnet, folder / "bin/Debug/net8.0/Probe.dll"


@pytest.mark.parametrize("target,expected", [
    (None, ["output"]),
    ("card", ["output", "card"]),
    ("card/data", ["output", "card"]),
    ("OUTPUT/data", ["output"]),
])
def test_gui_prepares_output_and_normalized_card_once(target_probe, tmp_path, target, expected):
    dotnet, probe = target_probe
    command = [dotnet, str(probe), str(tmp_path / "output")]
    if target:
        command.append(str(tmp_path / target))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(tmp_path / p) for p in expected]
