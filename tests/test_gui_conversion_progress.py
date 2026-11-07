"""Run the GUI's actual progress formatter without opening WinForms."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def progress_probe(tmp_path_factory):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip(".NET SDK required for GUI progress unit")
    folder = tmp_path_factory.mktemp("gui-progress-unit")
    source = Path(__file__).resolve().parents[1] / "gui/ConversionProgress.cs"
    (folder / "Probe.csproj").write_text('''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>
<ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup>
<ItemGroup><Compile Include="%s" Link="ConversionProgress.cs" /></ItemGroup></Project>'''
                                        % source.as_posix(), encoding="utf-8")
    (folder / "Program.cs").write_text('''using System.Text.Json;
using Kiln;
using var doc = JsonDocument.Parse(args[0]);
var p = ConversionProgress.Parse(doc.RootElement);
Console.WriteLine(JsonSerializer.Serialize(new { fraction = p?.Fraction, label = p?.Label, detail = p?.Detail }));
''', encoding="utf-8")
    built = subprocess.run([dotnet, "build", str(folder / "Probe.csproj"), "-nologo", "-v:quiet"],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    return dotnet, folder / "bin/Debug/net8.0/Probe.dll"


def probe(progress_probe, value):
    dotnet, dll = progress_probe
    r = subprocess.run([dotnet, str(dll), json.dumps(value)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_actual_counts_and_model_stage_remain_separate_from_time_estimates(progress_probe):
    p = probe(progress_probe, dict(phase="models", completed=3, total=20, elapsed_s=10000,
        active=[dict(model_id=7, model="synthetic.m2", stage="verify", work_done=5, work_total=80, stage_elapsed_s=900)]))
    assert p["fraction"] == 0.15
    assert p["label"] == "models 3/20"
    assert "synthetic.m2" in p["detail"] and "verify 5/80" in p["detail"]
    assert "900s" in p["detail"]


def test_unknown_totals_have_no_invented_fraction(progress_probe):
    p = probe(progress_probe, dict(phase="finish", completed=0, total=0, elapsed_s=999999, active=[]))
    assert p["fraction"] is None and p["label"] == "finish"


@pytest.mark.parametrize("bad", [None, {}, {"phase": "models", "completed": -1, "total": 20},
                                  {"phase": "models", "completed": 21, "total": 20}])
def test_malformed_snapshots_are_ignored(progress_probe, bad):
    assert probe(progress_probe, bad)["fraction"] is None
