from flask import Flask, render_template, request, redirect, send_from_directory, flash
from werkzeug.exceptions import RequestEntityTooLarge
import os
import sqlite3
import gpxpy
import srtm
import math
import secrets
from staticmap import StaticMap, Line

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB max upload size
app.secret_key = secrets.token_hex(32)  # Generate a random secret key at startup

from flask_compress import Compress

# This below command enables Gzip compression for the Flask app
# It compresses responses before sending them to clients,
# reducing data transfer and improves performance
Compress(app)

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# SQLite setup, support routes and cafes
def init_db():
    with sqlite3.connect('routes.db') as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            tags TEXT,
            gpx_file TEXT,
            distance REAL,
            elevation_gain REAL,
            start_location TEXT,
            difficulty TEXT,
            offroad INTEGER DEFAULT 0
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS cafes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            description TEXT,
            website TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def get_cafes_near_route(gpx_file, max_distance_m=2000):
    # Find cafes within max_distance_m meters of the route
    cafes_near_route = []
    
    # Get all cafes
    conn = sqlite3.connect('routes.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM cafes')
    all_cafes = c.fetchall()
    conn.close()
    
    if not all_cafes:
        return []
    
    # Parse GPX to get route points
    # Try except to avoid crashing
    try:
        with open(os.path.join(UPLOAD_FOLDER, gpx_file), 'r') as f:
            gpx = gpxpy.parse(f)
            route_points = []
            for track in gpx.tracks:
                for segment in track.segments:
                    for point in segment.points:
                        route_points.append((point.latitude, point.longitude))
    except:
        return []
    
    # Check each cafe against route points
    for cafe in all_cafes:
        min_distance = float('inf')
        for lat, lon in route_points:
            distance = haversine(cafe['latitude'], cafe['longitude'], lat, lon)
            # update minimum distance
            if distance < min_distance:
                min_distance = distance
        
        # cafe is close enough to the route
        if min_distance <= max_distance_m:
            cafe_dict = dict(cafe)
            cafe_dict['distance_to_route'] = min_distance
            cafes_near_route.append(cafe_dict)
    
    # Sort by distance to route
    cafes_near_route.sort(key=lambda x: x['distance_to_route'])
    return cafes_near_route

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
    
    # Get all cafes for the map
    c.execute('SELECT * FROM cafes')
    all_cafes_rows = c.fetchall()
    conn.close()
    
    if not route:
        return "Route not found", 404
    
    # Convert Row objects to dictionaries for JSON serialization
    all_cafes = [dict(row) for row in all_cafes_rows]
    
    # Get cafes near this route
    cafes_near_route = get_cafes_near_route(route['gpx_file'])
    
    return render_template('route.html', route=route, cafes_near_route=cafes_near_route, all_cafes=all_cafes)

@app.route('/')
def index():
    conn = sqlite3.connect('routes.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM routes")
    routes = c.fetchall()
    conn.close()
    return render_template('index.html', routes=routes, request=request)

@app.route('/cafes')
def cafes():
    # Get all cafes from the database
    conn = sqlite3.connect('routes.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM cafes ORDER BY name")
    cafes_rows = c.fetchall()
    conn.close()
    
    # Convert Row objects to dictionaries for JSON serialization
    cafes = [dict(row) for row in cafes_rows]
    
    return render_template('cafes.html', cafes=cafes)

@app.route('/cafe/add', methods=['GET', 'POST'])
def add_cafe():
    # add a new cafe
    if request.method == 'POST':
        name = request.form['name']
        latitude = float(request.form['latitude'])
        longitude = float(request.form['longitude'])
        description = request.form['description']
        website = request.form['website']
        
        with sqlite3.connect('routes.db') as conn:
            conn.execute('''INSERT INTO cafes (name, latitude, longitude, description, website) 
                           VALUES (?, ?, ?, ?, ?)''',
                        (name, latitude, longitude, description, website))
        
        flash("Cafe added successfully!")
        return redirect('/cafes')
    
    return render_template('add_cafe.html')

@app.route('/cafe/edit/<int:cafe_id>', methods=['GET', 'POST'])
def edit_cafe(cafe_id):
    # edit an existing cafe
    # bit risky as anyone can do this, but it's to keep it simple.
    if request.method == 'POST':
        name = request.form['name']
        latitude = float(request.form['latitude'])
        longitude = float(request.form['longitude'])
        description = request.form['description']
        website = request.form['website']
        
        with sqlite3.connect('routes.db') as conn:
            conn.execute('''UPDATE cafes SET name=?, latitude=?, longitude=?, description=?, website=? 
                           WHERE id=?''',
                        (name, latitude, longitude, description, website, cafe_id))

        flash("Cafe updated successfully!")
        return redirect('/cafes')
    
    # Get existing cafe data
    conn = sqlite3.connect('routes.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM cafes WHERE id = ?', (cafe_id,))
    cafe = c.fetchone()
    conn.close()
    
    if not cafe:
        flash("Cafe not found!")
        return redirect('/cafes')
    
    return render_template('edit_cafe.html', cafe=cafe)

@app.route('/route/edit/<int:route_id>', methods=['GET', 'POST'])
def edit_route(route_id):
    # edit an existing route
    # again a bit risky as anyone can do this, but it's to keep it simple.
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        tags = request.form['tags']
        
        with sqlite3.connect('routes.db') as conn:
            conn.execute('''UPDATE routes SET name=?, description=?, tags=? WHERE id=?''',
                        (name, description, tags, route_id))
        
        flash("Route updated successfully!")
        return redirect(f'/route/{route_id}')
    
    # Get existing route data
    conn = sqlite3.connect('routes.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM routes WHERE id = ?', (route_id,))
    route = c.fetchone()
    conn.close()
    
    if not route:
        flash("Route not found!")
        return redirect('/')
    
    return render_template('edit_route.html', route=route)

@app.route('/route/add', methods=['GET', 'POST'])
def add_route():
    if request.method == 'POST':
        file = request.files['gpx_file']
        name = request.form['name']
        description = request.form['description']
        tags = request.form['tags']
        offroad = int(request.form.get('offroad', '0'))
        
        # Handle cafe addition
        add_cafe_option = request.form.get('add_cafe', '0')
        cafe_name = request.form.get('cafe_name', '').strip()
        cafe_lat = request.form.get('cafe_latitude', '').strip()
        cafe_lon = request.form.get('cafe_longitude', '').strip()
        cafe_desc = request.form.get('cafe_description', '').strip()
        cafe_website = request.form.get('cafe_website', '').strip()

        # Check file size (already enforced by Flask, but for user-friendly error)
        file.seek(0, os.SEEK_END)
        file_length = file.tell()
        file.seek(0)
        if file_length > 5 * 1024 * 1024:
            flash("File is too large (max 5MB).")
            return render_template('add_route.html')

        # Check file extension
        ext = os.path.splitext(file.filename)[1].lower()
        if ext != '.gpx':
            flash("Only GPX files are allowed.")
            return render_template('add_route.html')

        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name.strip())[:40]

        # Insert a dummy row to get the next id
        with sqlite3.connect('routes.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO routes (name, description, tags, gpx_file, distance, elevation_gain, start_location, difficulty, offroad) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                      (name, description, tags, '', 0, 0, '', '', offroad))
            route_id = c.lastrowid

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

        # --- Difficulty rating logic ---
        # distance in km, elevation_gain in m
        difficulty = ""
        dist_km = distance / 1000
        elev_m = elevation_gain

        if dist_km < 30 and elev_m < 300:
            difficulty = "Easy"
        elif 30 <= dist_km <= 70 and 300 <= elev_m <= 1000:
            difficulty = "Moderate"
        elif 70 < dist_km <= 100 and 1000 < elev_m <= 2000:
            difficulty = "Hard"
        elif dist_km > 100 and elev_m > 2000:
            difficulty = "Very Hard"
        else:
            # If it doesn't fit exactly, pick the highest matching category
            if dist_km > 100 or elev_m > 2000:
                difficulty = "Very Hard"
            elif dist_km > 70 or elev_m > 1000:
                difficulty = "Hard"
            elif dist_km > 30 or elev_m > 300:
                difficulty = "Moderate"
            else:
                difficulty = "Easy"

        # --- Generate static map image with GPX overlay ---
        try:
            coords = []
            for track in gpx.tracks:
                for segment in track.segments:
                    for point in segment.points:
                        coords.append((point.longitude, point.latitude))
            if coords:
                m = StaticMap(400, 300)
                m.add_line(Line(coords, 'blue', 3))
                image = m.render()
                static_img_filename = f"{route_id}-{safe_name}.webp"
                static_img_path = os.path.join(UPLOAD_FOLDER, static_img_filename)
                # Convert to WebP and optimize
                image = image.convert("RGB")  # Ensure compatibility with WebP
                image.save(static_img_path, format="WEBP", quality=80, method=6)
            else:
                static_img_filename = ""
        except Exception as e:
            print("Static map generation failed:", e)
            static_img_filename = ""

        # Update the row with the real filename, stats, and difficulty, and static image
        with sqlite3.connect('routes.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE routes SET gpx_file=?, distance=?, elevation_gain=?, start_location=?, difficulty=? WHERE id=?',
                         (unique_filename, dist_km, elevation_gain, start_location, difficulty, route_id))
            
            # Add cafe if requested
            if add_cafe_option == '1' and cafe_name and cafe_lat and cafe_lon:
                try:
                    lat = float(cafe_lat)
                    lon = float(cafe_lon)
                    c.execute('''INSERT INTO cafes (name, latitude, longitude, description, website) 
                               VALUES (?, ?, ?, ?, ?)''',
                             (cafe_name, lat, lon, cafe_desc, cafe_website))
                    flash(f"Route uploaded and cafe '{cafe_name}' added successfully!")
                except ValueError:
                    flash("Route uploaded, but cafe coordinates were invalid.")
            elif add_cafe_option == '1':
                flash("Route uploaded, but cafe information was incomplete.")
            else:
                # General success message when no cafe is added
                flash("Route uploaded successfully!")

        return redirect('/')
    return render_template('add_route.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    flash("File is too large (max 5MB).")
    return render_template('add_route.html'), 413

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy-policy.html')

@app.route('/terms-of-service')
def terms_of_service():
    return render_template('terms-of-service.html')

@app.route('/about')
def about():
    return render_template('about.html')

if __name__ == '__main__':
    app.run(debug=True)