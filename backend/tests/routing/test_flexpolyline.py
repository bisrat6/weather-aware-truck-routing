import pytest
from hypothesis import given
from hypothesis import strategies as st

from routing.flexpolyline import decode
from tests.here_helpers import encode_polyline


def test_decodes_the_example_from_the_specification():
    # https://github.com/heremaps/flexible-polyline#example
    coords = decode("BFoz5xJ67i1B1B7PzIhaxL7Y")
    expected = [(50.10228, 8.69821), (50.10201, 8.69567), (50.10063, 8.69150), (50.09878, 8.68752)]
    assert len(coords) == 4
    for (lat, lon), (e_lat, e_lon) in zip(coords, expected, strict=True):
        assert lat == pytest.approx(e_lat, abs=1e-9)
        assert lon == pytest.approx(e_lon, abs=1e-9)


def test_header_only_means_no_points():
    assert decode("BF") == []


@pytest.mark.parametrize("text", ["", "B", "B!", "BFoz5xJ6 ", "CFoz5xJ"])
def test_malformed_strings_raise_value_error(text):
    with pytest.raises(ValueError):
        decode(text)


def test_truncated_value_raises():
    with pytest.raises(ValueError):
        decode("BFoz5xJ6g")  # last character has the continuation bit set


def test_incomplete_coordinate_raises():
    with pytest.raises(ValueError):
        decode("BFoz5xJ6")  # latitude without longitude


def test_third_dimension_is_read_and_dropped():
    coords = [(40.5, -111.25), (40.6, -111.35)]
    assert decode(encode_polyline(coords, precision=5, third=[10, 20])) == pytest.approx(coords)


@given(
    st.lists(
        st.tuples(st.floats(-90, 90), st.floats(-180, 180)), min_size=1, max_size=40
    ),
    st.integers(min_value=0, max_value=8),
)
def test_round_trip_within_precision(coords, precision):
    decoded = decode(encode_polyline(coords, precision=precision))
    assert len(decoded) == len(coords)
    for (lat, lon), (d_lat, d_lon) in zip(coords, decoded, strict=True):
        assert abs(lat - d_lat) <= 0.5 * 10**-precision + 1e-9
        assert abs(lon - d_lon) <= 0.5 * 10**-precision + 1e-9
