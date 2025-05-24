import os
import sqlite3
import gpxpy
import math

UPLOAD_FOLDER = 'uploads'
DB_PATH = 'routes.db'

# Coordinates for Campus and Leamington Spa
CAMPUS = (52.3813, -1.5616)      # University of Warwick
LEAMINGTON = (52.2922, -1.5354)  # Leamington Spa

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1 - a))

def determine_start_location(gpx):
    first_point = None
    for track in gpx.tracks:
        for segment in track.segments:
            if segment.points:
                first_point = segment.points[0]
                break
        if first_point:
            break

    if first_point:
        d_campus = haversine(first_point.latitude, first_point.longitude, *CAMPUS)
        d_leam = haversine(first_point.latitude, first_point.longitude, *LEAMINGTON)
        min_dist = min(d_campus, d_leam)
        if min_dist > 10000:
            return "Other"
        elif d_campus < d_leam:
            return "Campus"
        else:
            return "Leamington"
    else:
        return "Unknown"

def update_start_locations():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Ensure the start_location column exists
    try:
        c.execute('ALTER TABLE routes ADD COLUMN start_location TEXT')
        print("Added 'start_location' column to routes table.")
    except sqlite3.OperationalError:
        # Column already exists
        pass

    c.execute('SELECT id, gpx_file FROM routes')
    routes = c.fetchall()

    for route in routes:
        gpx_path = os.path.join(UPLOAD_FOLDER, route['gpx_file'])
        if not os.path.exists(gpx_path):
            print(f"GPX file not found: {gpx_path}")
            continue

        with open(gpx_path, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
            start_location = determine_start_location(gpx)

        c.execute('UPDATE routes SET start_location=? WHERE id=?', (start_location, route['id']))
        print(f"Route {route['id']}: start_location set to {start_location}")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    update_start_locations()