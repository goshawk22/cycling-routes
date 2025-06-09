import gpxpy
import overpy
import time
from shapely.geometry import Point
from xml.etree.ElementTree import Element, SubElement

# === Parameters ===
BATCH_SIZE = 50
BATCH_RADIUS = 7  # meters
DELAY_BETWEEN_BATCHES = 0.1  # seconds

# Surface and highway preferences
PREFER_PAVED = {"asphalt", "paved", "concrete"}
DEPRIORITIZE_TRACKS = {"track", "path", "footway", "pebblestone", "unpaved", "dirt", "gravel", "compacted", "fine_gravel", "ground", "grass"}

# === Load GPX points ===
with open("scripts/test-gravel.gpx", "r") as f:
    gpx = gpxpy.parse(f)

points = []
point_refs = []

for track in gpx.tracks:
    for segment in track.segments:
        for point in segment.points:
            points.append((point.latitude, point.longitude))
            point_refs.append(point)

# === Overpass Helper ===
def build_overpass_query(batch):
    queries = [
        f'way(around:50,{lat},{lon})["highway"];'
        for lat, lon in batch
    ]
    return f"""
    [out:json][timeout:25];
    (
        {''.join(queries)}
    );
    out tags center qt;
    """

def score_way(way, lat, lon):
    if way.center_lat is None or way.center_lon is None:
        return float("inf")

    dist = ((float(way.center_lat) - lat) ** 2 + (float(way.center_lon) - lon) ** 2) ** 0.5
    if dist < 5:
        score = 1
    else:
        score = dist

    highway = way.tags.get("highway", "")
    surface = way.tags.get("surface", "")

    if highway in {"primary", "secondary", "tertiary", "residential"}:
        score *= 0.5  # prefer car roads
    if highway in {"cycleway", "busway", "footway", "service", "unclassified"}:
        score *= 2
    if highway in DEPRIORITIZE_TRACKS:
        score *= 2.0
    if surface in PREFER_PAVED:
        score *= 0.5
    return score

# === Run Overpass Queries ===
api = overpy.Overpass()
surfaces = []

for i in range(0, len(points), BATCH_SIZE):
    batch = points[i:i + BATCH_SIZE]
    query = build_overpass_query(batch)

    # Retry logic
    while True:
        try:
            result = api.query(query)
            break
        except Exception as e:
            print(f"Overpass error: {e}. Retrying in 10s.")
            time.sleep(10)


    # Match each batch point to best way
    for (lat, lon) in batch:
        best_surface = "unknown"
        best_score = float("inf")

        for way in result.ways:
            if "surface" not in way.tags:
                if way.tags.get("highway", "") in {"primary", "secondary", "tertiary", "residential"}:
                    way.tags["surface"] = "paved"
                else:
                    continue
            s = score_way(way, lat, lon)
            if s < best_score:
                best_score = s
                best_surface = way.tags["surface"]

        surfaces.append(best_surface)

    print(f"Processed {i + len(batch)} / {len(points)}")
    time.sleep(DELAY_BETWEEN_BATCHES)

# === Add custom extension tags ===
def add_surface_extension(gpx_point, surface_value):
    ext = Element("extensions")
    surface_elem = SubElement(ext, "surface")
    surface_elem.text = surface_value
    gpx_point.extensions = [ext]

for pt, surface in zip(point_refs, surfaces):
    add_surface_extension(pt, surface)

# === Save output GPX ===
with open("output_with_surface.gpx", "w") as f:
    f.write(gpx.to_xml())
