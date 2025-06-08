from flask import Flask, render_template, request, redirect, send_from_directory
import os
import sqlite3
import gpxpy
import srtm
import math

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB max upload size

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# SQLite setup
def init_db():
    with sqlite3.connect('routes.db') as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            tags TEXT,
            gpx_file TEXT,
            distance REAL,
            elevation_gain REAL
        )''')
init_db()

def haversine(lat1, lon1, lat2, lon2):
    # Returns distance in meters
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1 - a))

# Coordinates for Campus and Leamington Spa
CAMPUS = (52.3813, -1.5616)      # University of Warwick
LEAMINGTON = (52.2922, -1.5354)  # Leamington Spa

@app.route('/route/<int:route_id>')
def route(route_id):
    conn = sqlite3.connect('routes.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM routes WHERE id = ?', (route_id,))
    route = c.fetchone()
    conn.close()
    if not route:
        return "Route not found", 404
    return render_template('route.html', route=route)

@app.route('/')
def index():
    conn = sqlite3.connect('routes.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Get filter parameters from query string
    search = request.args.get('search', '').strip()
    min_dist = request.args.get('min_distance')
    max_dist = request.args.get('max_distance')
    min_elev = request.args.get('min_elevation')
    max_elev = request.args.get('max_elevation')
    start_loc = request.args.get('start_location')

    query = "SELECT * FROM routes WHERE 1=1"
    params = []

    if search:
        query += " AND (name LIKE ? OR tags LIKE ?)"
        params += [f'%{search}%', f'%{search}%']
    if min_dist:
        query += " AND distance >= ?"
        params.append(float(min_dist))
    if max_dist:
        query += " AND distance <= ?"
        params.append(float(max_dist))
    if min_elev:
        query += " AND elevation >= ?"
        params.append(float(min_elev))
    if max_elev:
        query += " AND elevation <= ?"
        params.append(float(max_elev))
    if start_loc:
        query += " AND start_location = ?"
        params.append(start_loc)

    c.execute(query, params)
    routes = c.fetchall()
    conn.close()
    return render_template('index.html', routes=routes, request=request)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files['gpx_file']
        name = request.form['name']
        description = request.form['description']
        tags = request.form['tags']

        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name.strip())[:40]

        # Insert a dummy row to get the next id
        with sqlite3.connect('routes.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO routes (name, description, tags, gpx_file, distance, elevation_gain, start_location) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (name, description, tags, '', 0, 0, ''))
            route_id = c.lastrowid

        ext = os.path.splitext(file.filename)[1] or '.gpx'
        unique_filename = f"{route_id}-{safe_name}{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        file.save(filepath)

        # Parse GPX
        with open(filepath, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)

            # Replace all point elevations with SRTM data
            elevation_data = srtm.get_data()
            elevation_data.add_elevations = True
            for track in gpx.tracks:
                for segment in track.segments:
                    for point in segment.points:
                        srtm_elev = elevation_data.get_elevation(point.latitude, point.longitude)
                        if srtm_elev is not None:
                            point.elevation = srtm_elev

            distance = sum([t.length_3d() for t in gpx.tracks])
            elevation_gain = 0
            for track in gpx.tracks:
                for segment in track.segments:
                    uphill, _ = segment.get_uphill_downhill()
                    elevation_gain += uphill

        # After parsing GPX and before saving to DB
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
                start_location = "Other"
            elif d_campus < d_leam:
                start_location = "Campus"
            else:
                start_location = "Leamington"
        else:
            start_location = "Unknown"

        # Update the row with the real filename and stats
        with sqlite3.connect('routes.db') as conn:
            conn.execute('UPDATE routes SET gpx_file=?, distance=?, elevation_gain=?, start_location=? WHERE id=?',
                         (unique_filename, distance / 1000, elevation_gain, start_location, route_id))

        return redirect('/')
    return render_template('upload.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == '__main__':
    app.run(debug=True)