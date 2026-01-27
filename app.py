import os
import sqlite3
import math
import secrets
import re
import gpxpy
import srtm
import bleach
from flask import Flask, render_template, request, redirect, send_from_directory, flash, make_response
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import RequestEntityTooLarge
from staticmap import StaticMap, Line
from urllib.parse import urlparse
from elevation_utils import process_gpx_for_elevation

# Constants
UPLOAD_FOLDER = 'uploads'
DATABASE_PATH = 'routes.db'
MAX_CAFE_DISTANCE_M = 2000
EARTH_RADIUS_M = 6371000
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
UNKNOWN_LOCATION_THRESHOLD_M = 10000
STATIC_MAP_SIZE = (400, 300)
DEBUG = False  # Set to False in production

# Coordinates
CAMPUS = (52.3813, -1.5616)      # University of Warwick
LEAMINGTON = (52.2922, -1.5354)  # Leamington Spa
CALPE = (38.6425, 0.0422)        # Calpe, Spain

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE  # 5 MB max upload size
app.secret_key = secrets.token_hex(32)

# Enable Gzip compression
Compress(app)

# Setup rate limiting
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def sanitize_input(text, allow_links=False):
    """Sanitize user input to prevent XSS attacks."""
    if not text:
        return ""
    
    # Basic allowed tags for descriptions
    allowed_tags = ['p', 'br', 'strong', 'em', 'ul', 'ol', 'li']
    if allow_links:
        allowed_tags.extend(['a'])
    
    # Allowed attributes
    allowed_attributes = {}
    if allow_links:
        allowed_attributes['a'] = ['href', 'title']
    
    # Clean the input
    cleaned = bleach.clean(
        text.strip(),
        tags=allowed_tags,
        attributes=allowed_attributes,
        strip=True
    )
    
    return cleaned

def validate_coordinates(lat, lon):
    """Validate latitude and longitude coordinates."""
    try:
        lat_float = float(lat)
        lon_float = float(lon)
        
        if not (-90 <= lat_float <= 90):
            return False, "Latitude must be between -90 and 90"
        if not (-180 <= lon_float <= 180):
            return False, "Longitude must be between -180 and 180"
            
        return True, (lat_float, lon_float)
    except (ValueError, TypeError):
        return False, "Invalid coordinate format"

def validate_url(url):
    """Validate URL format."""
    if not url:
        return True  # Empty URL is allowed
    
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc]) and result.scheme in ['http', 'https']
    except:
        return False

def get_db_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database with required tables and indexes."""
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
        
        # Create indexes for better query performance
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_routes_difficulty ON routes(difficulty)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_routes_start_location ON routes(start_location)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_routes_offroad ON routes(offroad)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_routes_distance ON routes(distance)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_cafes_location ON cafes(latitude, longitude)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_cafes_name ON cafes(name)''')

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
    d_calpe = haversine(first_point.latitude, first_point.longitude, *CALPE)
    min_dist = min(d_campus, d_leam, d_calpe)
    
    if min_dist > UNKNOWN_LOCATION_THRESHOLD_M:
        return "Other"
    elif d_campus == min_dist:
        return "Campus"
    elif d_leam == min_dist:
        return "Leamington"
    else:
        return "Calpe"

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
    """Process GPX file and return route statistics with improved elevation calculation."""
    with open(filepath, 'r') as gpx_file:
        gpx = gpxpy.parse(gpx_file)

        # Use improved elevation calculation that matches Strava/Komoot better
        # This includes smoothing and filtering to reduce GPS noise
        distance, elevation_gain = process_gpx_for_elevation(gpx, method="distance_smooth_threshold")

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
            # Sanitize inputs
            name = sanitize_input(cafe_name.strip())
            desc = sanitize_input(cafe_desc.strip() if cafe_desc else '', allow_links=True)
            website = cafe_website.strip() if cafe_website else ''
            
            # Validate name length
            if len(name) > 100:
                return "Route uploaded, but cafe name was too long."
            
            # Validate coordinates
            valid_coords, coord_result = validate_coordinates(cafe_lat, cafe_lon)
            if not valid_coords:
                return f"Route uploaded, but cafe coordinates were invalid: {coord_result}"
            
            lat, lon = coord_result
            
            # Validate website URL if provided
            if website and not validate_url(website):
                return "Route uploaded, but cafe website URL was invalid."
            
            # Validate description length
            if len(desc) > 1000:
                return "Route uploaded, but cafe description was too long."
            
            conn.execute('''INSERT INTO cafes (name, latitude, longitude, description, website) 
                           VALUES (?, ?, ?, ?, ?)''',
                         (name, lat, lon, desc, website))
            return f"Route uploaded and cafe '{name}' added successfully!"
        except (ValueError, sqlite3.Error) as e:
            return f"Route uploaded, but cafe could not be added: {str(e)}"
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
@limiter.limit("2 per minute")
def add_cafe():
    """Add a new cafe."""
    if request.method == 'POST':
        # Get and sanitize inputs
        name = sanitize_input(request.form.get('name', '').strip())
        latitude_str = request.form.get('latitude', '').strip()
        longitude_str = request.form.get('longitude', '').strip()
        description = sanitize_input(request.form.get('description', ''), allow_links=True)
        website = request.form.get('website', '').strip()
        
        # Validate required fields
        if not name:
            flash("Cafe name is required.")
            return render_template('add_cafe.html')
        
        if len(name) > 100:
            flash("Cafe name must be less than 100 characters.")
            return render_template('add_cafe.html')
        
        # Validate coordinates
        valid_coords, coord_result = validate_coordinates(latitude_str, longitude_str)
        if not valid_coords:
            flash(f"Invalid coordinates: {coord_result}")
            return render_template('add_cafe.html')
        
        latitude, longitude = coord_result
        
        # Validate website URL if provided
        if website and not validate_url(website):
            flash("Invalid website URL format.")
            return render_template('add_cafe.html')
        
        # Validate description length
        if len(description) > 1000:
            flash("Description must be less than 1000 characters.")
            return render_template('add_cafe.html')
        
        try:
            with get_db_connection() as conn:
                conn.execute('''INSERT INTO cafes (name, latitude, longitude, description, website) 
                               VALUES (?, ?, ?, ?, ?)''',
                            (name, latitude, longitude, description, website))
            
            flash("Cafe added successfully!")
            return redirect('/cafes')
        except sqlite3.Error as e:
            flash(f"Database error: {str(e)}")
            return render_template('add_cafe.html')
    
    return render_template('add_cafe.html')

@app.route('/cafe/edit/<int:cafe_id>', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def edit_cafe(cafe_id):
    """Edit an existing cafe."""
    if request.method == 'POST':
        # Get and sanitize inputs
        name = sanitize_input(request.form.get('name', '').strip())
        latitude_str = request.form.get('latitude', '').strip()
        longitude_str = request.form.get('longitude', '').strip()
        description = sanitize_input(request.form.get('description', ''), allow_links=True)
        website = request.form.get('website', '').strip()
        
        # Validate required fields
        if not name:
            flash("Cafe name is required.")
            return redirect(f'/cafe/edit/{cafe_id}')
        
        if len(name) > 100:
            flash("Cafe name must be less than 100 characters.")
            return redirect(f'/cafe/edit/{cafe_id}')
        
        # Validate coordinates
        valid_coords, coord_result = validate_coordinates(latitude_str, longitude_str)
        if not valid_coords:
            flash(f"Invalid coordinates: {coord_result}")
            return redirect(f'/cafe/edit/{cafe_id}')
        
        latitude, longitude = coord_result
        
        # Validate website URL if provided
        if website and not validate_url(website):
            flash("Invalid website URL format.")
            return redirect(f'/cafe/edit/{cafe_id}')
        
        # Validate description length
        if len(description) > 1000:
            flash("Description must be less than 1000 characters.")
            return redirect(f'/cafe/edit/{cafe_id}')
        
        try:
            with get_db_connection() as conn:
                conn.execute('''UPDATE cafes SET name=?, latitude=?, longitude=?, description=?, website=? 
                               WHERE id=?''',
                            (name, latitude, longitude, description, website, cafe_id))

            flash("Cafe updated successfully!")
            return redirect('/cafes')
        except sqlite3.Error as e:
            flash(f"Database error: {str(e)}")
            return redirect(f'/cafe/edit/{cafe_id}')
    
    # Get existing cafe data
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM cafes WHERE id = ?', (cafe_id,))
            cafe = c.fetchone()
        
        if not cafe:
            flash("Cafe not found!")
            return redirect('/cafes')
        
        return render_template('edit_cafe.html', cafe=cafe)
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}")
        return redirect('/cafes')

@app.route('/route/edit/<int:route_id>', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def edit_route(route_id):
    """Edit an existing route with input validation and sanitization."""
    if request.method == 'POST':
        # Get and sanitize inputs
        name = sanitize_input(request.form.get('name', '').strip())
        description = sanitize_input(request.form.get('description', ''), allow_links=True)
        tags = sanitize_input(request.form.get('tags', '').strip())
        
        # Validate required fields
        if not name:
            flash("Route name is required.")
            return redirect(f'/route/edit/{route_id}')
        
        if len(name) > 100:
            flash("Route name must be less than 100 characters.")
            return redirect(f'/route/edit/{route_id}')
        
        if len(description) > 2000:
            flash("Description must be less than 2000 characters.")
            return redirect(f'/route/edit/{route_id}')
        
        if len(tags) > 200:
            flash("Tags must be less than 200 characters.")
            return redirect(f'/route/edit/{route_id}')
        
        try:
            with get_db_connection() as conn:
                conn.execute('''UPDATE routes SET name=?, description=?, tags=? WHERE id=?''',
                            (name, description, tags, route_id))
            
            flash("Route updated successfully!")
            return redirect(f'/route/{route_id}')
        except sqlite3.Error as e:
            flash(f"Database error: {str(e)}")
            return redirect(f'/route/edit/{route_id}')
    
    # Get existing route data
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM routes WHERE id = ?', (route_id,))
            route = c.fetchone()
        
        if not route:
            flash("Route not found!")
            return redirect('/')
        
        return render_template('edit_route.html', route=route)
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}")
        return redirect('/')

@app.route('/route/add', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def add_route():
    """Add a new route with GPX file upload, input validation and sanitization."""
    if request.method == 'POST':
        file = request.files.get('gpx_file')
        name = sanitize_input(request.form.get('name', '').strip())
        description = sanitize_input(request.form.get('description', ''), allow_links=True)
        tags = sanitize_input(request.form.get('tags', '').strip())
        offroad = request.form.get('offroad', '0')
        
        # Validate required fields
        if not file or not file.filename:
            flash("GPX file is required.")
            return render_template('add_route.html')
        
        if not name:
            flash("Route name is required.")
            return render_template('add_route.html')
        
        # Validate input lengths
        if len(name) > 100:
            flash("Route name must be less than 100 characters.")
            return render_template('add_route.html')
        
        if len(description) > 2000:
            flash("Description must be less than 2000 characters.")
            return render_template('add_route.html')
        
        if len(tags) > 200:
            flash("Tags must be less than 200 characters.")
            return render_template('add_route.html')
        
        # Validate offroad value
        try:
            offroad = int(offroad)
            if offroad not in [0, 1]:
                raise ValueError
        except (ValueError, TypeError):
            flash("Invalid offroad value.")
            return render_template('add_route.html')
        
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

        # Create safe filename (sanitize filename)
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)[:40]

        try:
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
            flash(f"Error processing GPX file: {str(e)}")
            return render_template('add_route.html')

        return redirect('/')
    
    return render_template('add_route.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded files with appropriate caching headers."""
    response = make_response(send_from_directory(UPLOAD_FOLDER, filename))
    
    # Set caching headers based on file type
    if filename.endswith(('.webp', '.png', '.jpg', '.jpeg')):
        # Cache images for 7 days
        response.headers['Cache-Control'] = 'public, max-age=604800'
    elif filename.endswith('.gpx'):
        # Cache GPX files for 1 day
        response.headers['Cache-Control'] = 'public, max-age=86400'
    else:
        # Default cache for 1 hour
        response.headers['Cache-Control'] = 'public, max-age=3600'
    
    return response

@app.after_request
def add_security_headers(response):
    """Add security and caching headers to all responses."""
    # Security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Cache static assets
    if request.endpoint == 'static':
        response.headers['Cache-Control'] = 'public, max-age=31536000'  # 1 year
    
    return response

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