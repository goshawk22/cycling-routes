import os
import sqlite3
import gpxpy
from staticmap import StaticMap, Line

UPLOAD_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'uploads'))
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'routes.db'))

def generate_static_map(gpx_path, img_path):
    try:
        with open(gpx_path, 'r') as f:
            gpx = gpxpy.parse(f)
        coords = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    coords.append((point.longitude, point.latitude))
        if coords:
            m = StaticMap(400, 300)
            m.add_line(Line(coords, 'blue', 3))
            image = m.render()
            # Convert to WebP and optimize
            image = image.convert("RGB")  # Ensure compatibility with WebP
            image.save(img_path, format="WEBP", quality=80, method=6)
            return True
    except Exception as e:
        print(f"Failed to generate static map for {gpx_path}: {e}")
    return False

for route in os.listdir(UPLOAD_FOLDER):
    if route.endswith('.gpx'):
        route_id = route.split('-')[0]
        route_name = route.replace('.gpx', '')
        gpx_path = os.path.join(UPLOAD_FOLDER, route)
        img_path = os.path.join(UPLOAD_FOLDER, f"{route_name}.webp")
        
        if generate_static_map(gpx_path, img_path):
            print(f"Static map generated for {route}")
        else:
            print(f"Failed to generate static map for {route}")