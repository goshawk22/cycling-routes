import os
import sqlite3
import gpxpy
import srtm
from elevation_utils import process_gpx_for_elevation

UPLOAD_FOLDER = 'uploads'
DB_PATH = 'routes.db'

def calculate_difficulty(distance_km, elevation_gain_m):
    """Calculate difficulty rating based on distance and elevation gain.
        I made these up based on routes I have done around warwick. 
        I.e. very hard is very hard for warwick, not say for Yorkshire."""
    if distance_km < 30 and elevation_gain_m < 300:
        return "Easy"
    elif 30 <= distance_km <= 70 and 300 <= elevation_gain_m <= 1000:
        return "Moderate"
    elif 70 < distance_km <= 100 and 1000 < elevation_gain_m <= 2000:
        return "Hard"
    elif distance_km > 100 and elevation_gain_m > 2000:
        return "Very Hard"
    else:
        # Pick the highest matching category
        if distance_km > 100 or elevation_gain_m > 2000:
            return "Very Hard"
        elif distance_km > 70 or elevation_gain_m > 1000:
            return "Hard"
        elif distance_km > 30 or elevation_gain_m > 300:
            return "Moderate"
        else:
            return "Easy"

def recalculate_elevation_with_srtm():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT id, gpx_file FROM routes')
    routes = c.fetchall()

    elevation_data = srtm.get_data()
    elevation_data.add_elevations = True

    for route in routes:
        gpx_path = os.path.join(UPLOAD_FOLDER, route['gpx_file'])
        if not os.path.exists(gpx_path):
            print(f"GPX file not found: {gpx_path}")
            continue

        with open(gpx_path, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
            distance, elevation_gain = process_gpx_for_elevation(gpx)
        
        # Calculate difficulty
        distance_km = distance / 1000
        difficulty = calculate_difficulty(distance_km, elevation_gain)
        
        # Update the route in the database
        c.execute(
            'UPDATE routes SET distance = ?, elevation_gain = ?, difficulty = ? WHERE id = ?',
            (distance_km, elevation_gain, difficulty, route['id'])
        )
        print(f"Updated route {route['id']}: distance={distance_km:.2f} km, elevation_gain={elevation_gain:.1f} m, difficulty={difficulty}")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    recalculate_elevation_with_srtm()