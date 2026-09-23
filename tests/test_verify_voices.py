"""Tests for plugin introspection — how a voice learns to drive a plugin."""

from synth_ui.tools.verify_voices import _control_ports, _patch_properties

SFIZZ_TTL = """
@prefix patch: <http://lv2plug.in/ns/ext/patch#> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .

<http://sfztools.github.io/sfizz:sfzfile>
    a lv2:Parameter ;
    rdfs:label "SFZ file" .

<http://sfztools.github.io/sfizz>
    a lv2:Plugin ;
    patch:writable <http://sfztools.github.io/sfizz:sfzfile> .
"""


def bundle(tmp_path, ttl, name="manifest.ttl"):
    (tmp_path / name).write_text(ttl)
    return str(tmp_path)


def test_finds_a_patch_writable_property(tmp_path):
    props = _patch_properties(bundle(tmp_path, SFIZZ_TTL))
    assert props == [("http://sfztools.github.io/sfizz:sfzfile", "SFZ file")]


def test_labels_come_from_the_declaration_not_the_reference(tmp_path):
    # Anchoring on any occurrence of the URI hits the patch:writable line, where
    # the neighbouring property's label is the next thing in the window — every
    # property then reports the first one's name.
    ttl = """
<urn:plug> patch:writable <urn:a> , <urn:b> ; a lv2:Plugin .
<urn:a> a lv2:Parameter ; rdfs:label "Soundfont" .
<urn:b> a lv2:Parameter ; rdfs:label "Bank" .
"""
    assert _patch_properties(bundle(tmp_path, ttl)) == [
        ("urn:a", "Soundfont"),
        ("urn:b", "Bank"),
    ]


def test_a_plugin_with_no_file_property_reports_none(tmp_path):
    ttl = "<urn:plug> a lv2:Plugin ; lv2:port [ lv2:symbol \"gain\" ] .\n"
    assert _patch_properties(bundle(tmp_path, ttl)) == []


def test_scans_every_ttl_in_the_bundle(tmp_path):
    # Plugins commonly split manifest.ttl from <plugin>.ttl; the property is
    # usually in the latter.
    (tmp_path / "manifest.ttl").write_text("<urn:plug> a lv2:Plugin .\n")
    (tmp_path / "dsp.ttl").write_text(
        "<urn:plug> patch:writable <urn:sf> .\n"
        '<urn:sf> a lv2:Parameter ; rdfs:label "SF" .\n'
    )
    assert _patch_properties(str(tmp_path)) == [("urn:sf", "SF")]


def test_a_missing_bundle_is_not_an_error(tmp_path):
    assert _patch_properties(str(tmp_path / "nope.lv2")) == []


# --- control ports ----------------------------------------------------------

LV2INFO = """
    Port 4:
        Type:        http://lv2plug.in/ns/lv2core#ControlPort
                     http://lv2plug.in/ns/lv2core#InputPort
        Symbol:      level_in
        Name:        Input Gain

    Port 7:
        Type:        http://lv2plug.in/ns/lv2core#ControlPort
                     http://lv2plug.in/ns/lv2core#OutputPort
        Symbol:      meter_inL
        Name:        Meter-InL

    Port 9:
        Type:        http://lv2plug.in/ns/lv2core#AudioPort
                     http://lv2plug.in/ns/lv2core#InputPort
        Symbol:      in_l
        Name:        In L
"""


ENUM_LV2INFO = """
    Port 2:
        Type:        http://lv2plug.in/ns/lv2core#ControlPort
                     http://lv2plug.in/ns/lv2core#InputPort
        Scale Points:
            0 = "Shiny1"
            5 = "Blah"

        Symbol:      o1wave
        Name:        Osc1 Wave
        Minimum:     0.000000
        Maximum:     28.000000
"""


def test_a_port_with_scale_points_is_still_listed():
    """lv2info puts a blank line between a port's Scale Points and its Symbol.
    Splitting records there hid every enumerated control from the tool that
    exists to report them — including the wavetable selector this reported as
    absent from Calf Wavetable."""
    assert _control_ports(ENUM_LV2INFO) == [("o1wave", "Osc1 Wave")]


def test_only_input_control_ports_are_listed():
    # Output controls are meters and LEDs — nothing a voice can set — and audio
    # ports aren't parameters at all.
    assert _control_ports(LV2INFO) == [("level_in", "Input Gain")]


# --- prefixed turtle names (the common style) -------------------------------

FLUIDA_TTL = """
@prefix lv2: <http://lv2plug.in/ns/lv2core#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix atom:  <http://lv2plug.in/ns/ext/atom#> .
@prefix patch: <http://lv2plug.in/ns/ext/patch#> .
@prefix fluida:  <https://github.com/brummer10/Fluida.lv2#>  .
@prefix mod: <http://moddevices.com/ns/mod#> .

fluida:soundfont
    a lv2:Parameter ;
    mod:fileTypes "sf2" ;
    rdfs:label "soundfont" ;
    rdfs:range atom:Path .

fluida:gain
    a lv2:Parameter ;
    rdfs:label "Gain" ;
    rdfs:range atom:Float .

<https://github.com/brummer10/Fluida.lv2>
    a lv2:Plugin ;
    patch:writable fluida:soundfont ,
                fluida:gain ;
    patch:readable fluida:gain .
"""


def test_prefixed_names_are_expanded_against_at_prefix(tmp_path):
    # Matching only <full URIs> reported "no properties" for a plugin whose
    # entire parameter set — the soundfont path included — is written as
    # `fluida:soundfont`. That hid the one thing this function exists to find.
    props = dict(_patch_properties(bundle(tmp_path, FLUIDA_TTL, "Fluida.ttl")))
    assert "https://github.com/brummer10/Fluida.lv2#soundfont" in props
    assert "https://github.com/brummer10/Fluida.lv2#gain" in props


def test_file_taking_properties_are_marked(tmp_path):
    # Which property a voice's `path` goes through is the whole question; the
    # mod:fileTypes hint answers it without reading the turtle by hand.
    props = dict(_patch_properties(bundle(tmp_path, FLUIDA_TTL, "Fluida.ttl")))
    assert "[file: sf2]" in props["https://github.com/brummer10/Fluida.lv2#soundfont"]
    assert "file" not in props["https://github.com/brummer10/Fluida.lv2#gain"]


def test_readable_only_properties_are_not_listed(tmp_path):
    # patch:readable without patch:writable can't be set, so it isn't a `param`.
    ttl = """
@prefix p: <urn:p#> .
p:ro a lv2:Parameter ; rdfs:label "Read Only" .
<urn:plug> a lv2:Plugin ; patch:readable p:ro .
"""
    assert _patch_properties(bundle(tmp_path, ttl)) == []
