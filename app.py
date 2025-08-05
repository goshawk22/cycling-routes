import os
import sqlite3
import math
import secrets
import re
import gpxpy
import srtm
from flask import Flask, render_template, request, redirect, send_from_directory, flash
from flask_compress import Compress
from werkzeug.exceptions import RequestEntityTooLarge
from staticmap import StaticMap, Line

# Constants
UPLOAD_FOLDER = 'uploads'
DATABASE_PATH = 'routes.db'
MAX_CAFE_DISTANCE_M = 2000
EARTH_RADIUS_M = 6371000
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
UNKNOWN_LOCATION_THRESHOLD_M = 10000
STATIC_MAP_SIZE = (400, 300)
DEBUG = True  # Set to False in production

# Coordinates
CAMPUS = (52.3813, -1.5616)      # University of Warwick
LEAMINGTON = (52.2922, -1.5354)  # Leamington Spa

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE  # 5 MB max upload size
app.secret_key = secrets.token_hex(32)

# Enable Gzip compression
Compress(app)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database with required tables."""
    with get_db_connection() as conn:
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
    """Calculate the distance between two locations in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def get_cafes_near_route(gpx_file, max_distance_m=MAX_CAFE_DISTANCE_M):
    """Find cafes within max_distance_m meters of the route."""
    cafes_near_route = []
    
    # Get all cafes
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM cafes')
        all_cafes = c.fetchall()
    
    if not all_cafes:
        return []
    
    # Parse GPX to get route points
    try:
        with open(os.path.join(UPLOAD_FOLDER, gpx_file), 'r') as f:
            gpx = gpxpy.parse(f)
            route_points = []
            for track in gpx.tracks:
                for segment in track.segments:
                    for point in segment.points:
                        route_points.append((point.latitude, point.longitude))
    except Exception as e:
        print(f"Error parsing GPX file {gpx_file}: {e}")
        return []
    
    # Check each cafe against route points
    for cafe in all_cafes:
        min_distance = float('inf')
        for lat, lon in route_points:
            distance = haversine(cafe['latitude'], cafe['longitude'], lat, lon)
            if distance < min_distance:
                min_distance = distance
        
        if min_distance <= max_distance_m:
            cafe_dict = dict(cafe)
            cafe_dict['distance_to_route'] = min_distance
            cafes_near_route.append(cafe_dict)
    
    # Sort by distance to route
    cafes_near_route.sort(key=lambda x: x['distance_to_route'])
    return cafes_near_route

def determine_start_location(first_point):
    """Determine the start location based on proximity to preset locations."""
    if not first_point:
        return "Unknown"
    
    d_campus = haversine(first_point.latitude, first_point.longitude, *CAMPUS)
    d_leam = haversine(first_point.latitude, first_point.longitude, *LEAMINGTON)
    min_dist = min(d_campus, d_leam)
    
    if min_dist > UNKNOWN_LOCATION_THRESHOLD_M:
        return "Other"
    elif d_campus < d_leam:
        return "Campus"
    else:
        return "Leamington"

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

def process_gpx_file(filepath):
    """Process GPX file and return route statistics."""
    with open(filepath, 'r') as gpx_file:
        gpx = gpxpy.parse(gpx_file)

        # Replace all point elevations with SRTM data
        # Common standard for all files so at least it's consistent
        # Doesn't match up with strava but then everyone seems to calculate it differently (I'm looking at you Komoot)
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

        # Get first point for location determination
        first_point = None
        for track in gpx.tracks:
            for segment in track.segments:
                if segment.points:
                    first_point = segment.points[0]
                    break
            if first_point:
                break

        return gpx, distance, elevation_gain, first_point

def generate_static_map(gpx, route_id, safe_name):
    """Generate a static map image from GPX data.
        Loads faster on the index page."""
    try:
        coords = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    coords.append((point.longitude, point.latitude))
        
        if coords:
            m = StaticMap(*STATIC_MAP_SIZE)
            m.add_line(Line(coords, 'blue', 3))
            image = m.render()
            static_img_filename = f"{route_id}-{safe_name}.webp"
            static_img_path = os.path.join(UPLOAD_FOLDER, static_img_filename)
            # Convert to WebP and optimize
            image = image.convert("RGB")
            image.save(static_img_path, format="WEBP", quality=80, method=6)
            return static_img_filename
        else:
            return ""
    except Exception as e:
        print(f"Static map generation failed: {e}")
        return ""

def add_cafe_if_requested(conn, add_cafe_option, cafe_name, cafe_lat, cafe_lon, cafe_desc, cafe_website):
    """Add a cafe if the option is selected and data is valid."""
    if add_cafe_option == '1' and cafe_name and cafe_lat and cafe_lon:
        try:
            lat = float(cafe_lat)
            lon = float(cafe_lon)
            conn.execute('''INSERT INTO cafes (name, latitude, longitude, description, website) 
                           VALUES (?, ?, ?, ?, ?)''',
                         (cafe_name, lat, lon, cafe_desc, cafe_website))
            return f"Route uploaded and cafe '{cafe_name}' added successfully!"
        except ValueError:
            return "Route uploaded, but cafe coordinates were invalid."
    elif add_cafe_option == '1':
        return "Route uploaded, but cafe information was incomplete."
    else:
        return "Route uploaded successfully!"

@app.route('/route/<int:route_id>')
def route(route_id):
    """Display a specific route with its details and nearby cafes."""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM routes WHERE id = ?', (route_id,))
        route = c.fetchone()
        
        # Get all cafes for the map
        c.execute('SELECT * FROM cafes')
        all_cafes_rows = c.fetchall()
    
    if not route:
        return "Route not found", 404
    
    # Convert Row objects to dictionaries for JSON serialization
    all_cafes = [dict(row) for row in all_cafes_rows]
    
    # Get cafes near this route
    cafes_near_route = get_cafes_near_route(route['gpx_file'])
    
    return render_template('route.html', route=route, cafes_near_route=cafes_near_route, all_cafes=all_cafes)

@app.route('/')
def index():
    """Display the main page with all routes."""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM routes")
        routes = c.fetchall()
    return render_template('index.html', routes=routes, request=request)

@app.route('/cafes')
def cafes():
    """Display all cafes."""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM cafes ORDER BY name")
        cafes_rows = c.fetchall()
    
    # Convert Row objects to dictionaries for JSON serialization
    cafes = [dict(row) for row in cafes_rows]
    
    return render_template('cafes.html', cafes=cafes)

@app.route('/cafe/add', methods=['GET', 'POST'])
def add_cafe():
    """Add a new cafe."""
    if request.method == 'POST':
        name = request.form['name']
        latitude = float(request.form['latitude'])
        longitude = float(request.form['longitude'])
        description = request.form['description']
        website = request.form['website']
        
        with get_db_connection() as conn:
            conn.execute('''INSERT INTO cafes (name, latitude, longitude, description, website) 
                           VALUES (?, ?, ?, ?, ?)''',
                        (name, latitude, longitude, description, website))
        
        flash("Cafe added successfully!")
        return redirect('/cafes')
    
    return render_template('add_cafe.html')

@app.route('/cafe/edit/<int:cafe_id>', methods=['GET', 'POST'])
def edit_cafe(cafe_id):
    """Edit an existing cafe."""
    if request.method == 'POST':
        name = request.form['name']
        latitude = float(request.form['latitude'])
        longitude = float(request.form['longitude'])
        description = request.form['description']
        website = request.form['website']
        
        with get_db_connection() as conn:
            conn.execute('''UPDATE cafes SET name=?, latitude=?, longitude=?, description=?, website=? 
                           WHERE id=?''',
                        (name, latitude, longitude, description, website, cafe_id))

        flash("Cafe updated successfully!")
        return redirect('/cafes')
    
    # Get existing cafe data
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM cafes WHERE id = ?', (cafe_id,))
        cafe = c.fetchone()
    
    if not cafe:
        flash("Cafe not found!")
        return redirect('/cafes')
    
    return render_template('edit_cafe.html', cafe=cafe)

@app.route('/route/edit/<int:route_id>', methods=['GET', 'POST'])
def edit_route(route_id):
    """Edit an existing route."""
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        tags = request.form['tags']
        
        with get_db_connection() as conn:
            conn.execute('''UPDATE routes SET name=?, description=?, tags=? WHERE id=?''',
                        (name, description, tags, route_id))
        
        flash("Route updated successfully!")
        return redirect(f'/route/{route_id}')
    
    # Get existing route data
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM routes WHERE id = ?', (route_id,))
        route = c.fetchone()
    
    if not route:
        flash("Route not found!")
        return redirect('/')
    
    return render_template('edit_route.html', route=route)

@app.route('/route/add', methods=['GET', 'POST'])
def add_route():
    """Add a new route with GPX file upload."""
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

        # Validate file size
        file.seek(0, os.SEEK_END)
        file_length = file.tell()
        file.seek(0)
        if file_length > MAX_FILE_SIZE:
            flash("File is too large (max 5MB).")
            return render_template('add_route.html')

        # Validate file extension
        # We could support more formats in the future, but for now we only support GPX
        ext = os.path.splitext(file.filename)[1].lower()
        if ext != '.gpx':
            flash("Only GPX files are allowed.")
            return render_template('add_route.html')

        # Create safe filename
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name.strip())[:40]

        # Insert a dummy row to get the next id
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('INSERT INTO routes (name, description, tags, gpx_file, distance, elevation_gain, start_location, difficulty, offroad) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                      (name, description, tags, '', 0, 0, '', '', offroad))
            route_id = c.lastrowid

        # Save file
        unique_filename = f"{route_id}-{safe_name}{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        file.save(filepath)

        try:
            # Process GPX file
            gpx, distance, elevation_gain, first_point = process_gpx_file(filepath)
            
            # Calculate route statistics
            dist_km = distance / 1000
            start_location = determine_start_location(first_point)
            difficulty = calculate_difficulty(dist_km, elevation_gain)
            
            # Generate static map
            static_img_filename = generate_static_map(gpx, route_id, safe_name)

            # Update the database with route data
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute('UPDATE routes SET gpx_file=?, distance=?, elevation_gain=?, start_location=?, difficulty=? WHERE id=?',
                         (unique_filename, dist_km, elevation_gain, start_location, difficulty, route_id))
                
                # Add cafe if requested
                message = add_cafe_if_requested(conn, add_cafe_option, cafe_name, cafe_lat, cafe_lon, cafe_desc, cafe_website)
                flash(message)

        except Exception as e:
            flash(f"Error processing GPX file: {e}")
            return render_template('add_route.html')

        return redirect('/')
    
    return render_template('add_route.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded files."""
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    """Handle file upload size errors."""
    flash("File is too large (max 5MB).")
    return render_template('add_route.html'), 413

@app.route('/privacy-policy')
def privacy_policy():
    """Display privacy policy page."""
    return render_template('privacy-policy.html')

@app.route('/terms-of-service')
def terms_of_service():
    """Display terms of service page."""
    return render_template('terms-of-service.html')

@app.route('/about')
def about():
    """Display about page."""
    return render_template('about.html')

if __name__ == '__main__':
    app.run(debug=DEBUG)