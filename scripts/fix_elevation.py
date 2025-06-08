import os
import sqlite3
import gpxpy
import srtm

UPLOAD_FOLDER = 'uploads'
DB_PATH = 'routes.db'

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
            elevation_gain = 0.0
            # Replace all point elevations with SRTM data
            for track in gpx.tracks:
                for segment in track.segments:
                    for i, point in enumerate(segment.points):
                        srtm_elev = elevation_data.get_elevation(point.latitude, point.longitude)
                        if srtm_elev is not None:
                            point.elevation = srtm_elev

            distance = sum([t.length_3d() for t in gpx.tracks])
            for track in gpx.tracks:
                for segment in track.segments:
                    uphill, _ = segment.get_uphill_downhill()
                    elevation_gain += uphill

        # Update the route in the database
        c.execute(
            'UPDATE routes SET distance = ?, elevation_gain = ? WHERE id = ?',
            (distance / 1000, elevation_gain, route['id'])
        )
        print(f"Updated route {route['id']}: distance={distance/1000:.2f} km, elevation_gain={elevation_gain:.1f} m (SRTM)")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    recalculate_elevation_with_srtm()