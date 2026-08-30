"""
Enhetstester for de rene parse-funksjonene i fetch_geodata.py: GML->shapely,
akserekkefolge, dybdeattributt-gjenkjenning og raadata-dumping
(data/_raw/). Ingen nettverk involvert - XML-utdragene er haandskrevet
etter OGC GML/WFS-spesifikasjonen, IKKE hentet fra den ekte tjenesten (som
var utilgjengelig fra utviklingsmiljoet). Kjor disse pa nytt mot data/_raw/
foerste gang fetch_geodata.py faktisk kjores mot Kartverket, og juster om
noe her ikke stemmer med virkeligheten.
"""

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("shapely")

import fetch_geodata as F


def test_gml_linestring_poslist():
    xml = """<gml:LineString xmlns:gml="http://www.opengis.net/gml">
      <gml:posList srsDimension="2">9.5 58.8 9.6 58.85 9.7 58.9</gml:posList>
    </gml:LineString>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert g.geom_type == "LineString"
    assert list(g.coords) == [(9.5, 58.8), (9.6, 58.85), (9.7, 58.9)]


def test_gml_polygon_legacy_coordinates():
    xml = """<gml:Polygon xmlns:gml="http://www.opengis.net/gml">
      <gml:exterior><gml:LinearRing>
        <gml:coordinates>9.0,58.0 9.1,58.0 9.1,58.1 9.0,58.1 9.0,58.0</gml:coordinates>
      </gml:LinearRing></gml:exterior>
    </gml:Polygon>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert g.geom_type == "Polygon"
    assert g.exterior.coords[0] == (9.0, 58.0)
    assert len(list(g.exterior.coords)) == 5


def test_gml_multicurve():
    xml = """<gml:MultiCurve xmlns:gml="http://www.opengis.net/gml">
      <gml:curveMember><gml:LineString><gml:posList>9.0 58.0 9.1 58.1</gml:posList></gml:LineString></gml:curveMember>
      <gml:curveMember><gml:LineString><gml:posList>9.2 58.2 9.3 58.3</gml:posList></gml:LineString></gml:curveMember>
    </gml:MultiCurve>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert g.geom_type == "MultiLineString"
    assert len(g.geoms) == 2


def test_gml_srs_dimension_3_ignorerer_z():
    xml = """<gml:LineString xmlns:gml="http://www.opengis.net/gml">
      <gml:posList srsDimension="3">9.5 58.8 0 9.6 58.85 0</gml:posList>
    </gml:LineString>"""
    g = F.gml_to_shapely(ET.fromstring(xml))
    assert list(g.coords) == [(9.5, 58.8), (9.6, 58.85)]


def test_axis_order_urn_geografisk_er_latlon():
    assert F.axis_order_is_latlon("urn:ogc:def:crs:EPSG::4326") is True
    assert F.axis_order_is_latlon("urn:ogc:def:crs:EPSG::4258") is True


def test_axis_order_plain_epsg_er_lonlat():
    assert F.axis_order_is_latlon("EPSG:4326") is False
    assert F.axis_order_is_latlon("EPSG:25832") is False


def test_axis_order_urn_projisert_er_lonlat():
    assert F.axis_order_is_latlon("urn:ogc:def:crs:EPSG::25832") is False


def test_iter_members_og_parse_member():
    xml = """<?xml version="1.0"?>
    <wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
                            xmlns:gml="http://www.opengis.net/gml/3.2"
                            xmlns:app="http://example.org/app">
      <wfs:member>
        <app:Dybdekurve gml:id="dk.1">
          <app:dybde>20.0</app:dybde>
          <app:geometri><gml:LineString><gml:posList>9.40 58.90 9.45 58.95</gml:posList></gml:LineString></app:geometri>
        </app:Dybdekurve>
      </wfs:member>
    </wfs:FeatureCollection>"""
    root = ET.fromstring(xml)
    members = list(F._iter_members(root))
    assert len(members) == 1
    geom, props = F._parse_gml_member(members[0])
    assert geom.geom_type == "LineString"
    assert props["dybde"] == "20.0"


def test_detect_depth_property_finner_norsk_navn():
    props = [{"dybde": "20", "objtype": "Dybdekurve"}]
    assert F.detect_depth_property(props) == "dybde"


def test_detect_depth_property_ingen_treff():
    props = [{"objtype": "Kystkontur", "navn": "x"}]
    assert F.detect_depth_property(props) is None


def test_normalize_depth_properties_setter_depth_m():
    from shapely.geometry import LineString
    feats = [
        (LineString([(9, 58), (9.1, 58.1)]), {"dybde": "20"}),
        (LineString([(9, 58), (9.1, 58.1)]), {"dybde": "-30"}),  # negativ dyp-konvensjon
    ]
    out, key = F.normalize_depth_properties(feats)
    assert key == "dybde"
    assert out[0][1]["depth_m"] == 20.0
    assert out[1][1]["depth_m"] == 30.0  # abs()


def test_match_layer_eksakt_foretrekkes_over_delvis():
    names = ["app:Kystkontur", "app:Kystkontur_grovt"]
    assert F.match_layer(names, ["kystkontur", "kyst"]) == "app:Kystkontur"


def test_match_layer_delvis_naar_ikke_eksakt():
    names = ["app:DybdekurveGrunnkart"]
    assert F.match_layer(names, ["dybdekurve"]) == "app:DybdekurveGrunnkart"


def test_match_layer_ingen_treff():
    assert F.match_layer(["app:Noe_annet"], ["dybdekurve"]) is None


# ------------------------------------------------- raadata-dumping (aldri stille)


def test_ext_for_gjetter_ut_fra_content_type():
    assert F._ext_for("application/json; charset=utf-8") == ".json"
    assert F._ext_for("text/xml") == ".xml"
    assert F._ext_for("application/gml+xml") == ".xml"
    assert F._ext_for("text/html") == ".html"
    assert F._ext_for(None) == ".bin"
    assert F._ext_for("application/octet-stream") == ".bin"


def test_slug_er_filsystem_trygg():
    s = F._slug("https://wfs.geonorge.no/skwms1/wfs.dybdedata2 v2.0.0")
    assert s.replace("_", "").isalnum()
    assert " " not in s and "/" not in s and ":" not in s


def test_dump_call_skriver_meta_ved_feil_uten_respons(tmp_path):
    F._dump_call(tmp_path, 1, "capabilities_test", "https://example/wfs?x=1",
                 status=None, error="ConnectionError: boom", content=None)
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    meta = files[0].read_text()
    assert "https://example/wfs?x=1" in meta
    assert "ingen HTTP-respons" in meta
    assert "boom" in meta


def test_dump_call_skriver_meta_og_body_ved_suksess(tmp_path):
    F._dump_call(tmp_path, 2, "getfeature_json_p1", "https://example/wfs?x=2",
                 status=200, error=None, content=b'{"type":"FeatureCollection"}',
                 content_type="application/json")
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["002_getfeature_json_p1.json", "002_getfeature_json_p1.meta.txt"]
    body = (tmp_path / "002_getfeature_json_p1.json").read_bytes()
    assert body == b'{"type":"FeatureCollection"}'
    meta = (tmp_path / "002_getfeature_json_p1.meta.txt").read_text()
    assert "status: 200" in meta


def test_dump_call_uten_dump_dir_er_no_op():
    # skal ikke krasje naar dump_dir=None (f.eks. hvis noen kaller
    # funksjonene direkte uten aa ha satt opp data/_raw/)
    F._dump_call(None, 1, "tag", "url", 200, None, b"data", "text/plain")
