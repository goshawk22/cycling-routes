import math

def moving_average_filter(elevations, window_size):
    """
    Apply a moving average filter to elevation data.
    """
    if len(elevations) < window_size:
        return elevations.copy()
    
    smoothed = []
    half_window = window_size // 2
    
    for i in range(len(elevations)):
        start = max(0, i - half_window)
        end = min(len(elevations), i + half_window + 1)
        window_values = elevations[start:end]
        smoothed.append(sum(window_values) / len(window_values))
    
    return smoothed

def distance_based_smoothing(points, smooth_distance):
    """
    For each point we average elevations of points within +/- smooth_distance
    along the route using cumulative distances and a sliding window. This
    produces behavior similar to the original distance-based smoother but
    runs faster.
    """
    n = len(points)
    if n < 2:
        return [p[2] for p in points]

    # Build cumulative distances along the route
    cumdist = [0.0] * n
    for i in range(1, n):
        cumdist[i] = cumdist[i - 1] + haversine_distance(points[i - 1][0], points[i - 1][1],
                                                         points[i][0], points[i][1])

    smoothed = [0.0] * n
    left = 0
    right = 0

    for i in range(n):
        min_d = cumdist[i] - smooth_distance
        max_d = cumdist[i] + smooth_distance

        # advance left to the first index with cumdist[left] >= min_d
        while left < n and cumdist[left] < min_d:
            left += 1

        # advance right while next point is within max_d
        while right + 1 < n and cumdist[right + 1] <= max_d:
            right += 1

        if left <= right:
            # simple average across window
            s = 0.0
            for j in range(left, right + 1):
                s += points[j][2]
            smoothed[i] = s / (right - left + 1)
        else:
            smoothed[i] = points[i][2]

    return smoothed

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance between two points on Earth in meters."""
    R = 6371000  # Earth radius in meters
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) ** 2 + 
         math.cos(lat1_rad) * math.cos(lat2_rad) * 
         math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

def calculate_elevation_gain(points, distance, method = "distance_smooth_threshold"):
    """
    Calculate elevation gain using improved methods that reduce GPS noise.
    
    Args:
        points: List of (latitude, longitude, elevation) tuples
        method: Calculation method to use:
            - "distance_smooth_threshold": Distance-based smoothing + threshold (works well when MAV fails on hilly routes)
            - "moving_average_threshold": Moving average + threshold (generally works well)
            - "best_of_both": Use moving average for flat routes and distance-based for hilly routes
    Returns:
        Total elevation gain in meters
    """
    if len(points) < 2:
        return 0.0
    
    elevations = [point[2] for point in points]
    
    if method == "distance_smooth_threshold":
        # Distance-based smoothing (recommended for cycling routes)
        smooth_distance = 25.0
        threshold = 2.0
        smoothed_elevations = distance_based_smoothing(points, smooth_distance=smooth_distance)
        return _calculate_gain_with_threshold(smoothed_elevations, threshold=threshold)

    elif method == "moving_average_threshold":
        # Moving average with fixed threshold
        window_size = 3
        threshold = 1.0
        smoothed_elevations = moving_average_filter(elevations, window_size=window_size)
        return _calculate_gain_with_threshold(smoothed_elevations, threshold=threshold)
    
    elif method == "best_of_both":
        # Moving average with fixed threshold
        window_size = 3
        threshold = 1.0
        smoothed_elevations = moving_average_filter(elevations, window_size=window_size)
        total_gain_ma = _calculate_gain_with_threshold(smoothed_elevations, threshold=threshold)
   
        # Distance-based smoothing
        smooth_distance = 25.0
        threshold = 2.0
        smoothed_elevations = distance_based_smoothing(points, smooth_distance=smooth_distance)
        total_gain_db = _calculate_gain_with_threshold(smoothed_elevations, threshold=threshold)

        best_result = 0
        if total_gain_ma / distance * 1000 > 11:
            best_result = total_gain_db
        else:
            best_result = total_gain_ma


        return best_result

    else:
        raise ValueError(f"Unknown method: {method}")

def _calculate_gain_with_threshold(elevations, threshold):
    """
    Calculate elevation gain with a minimum threshold to filter out noise.
    Only count elevation changes above the threshold.
    """
    if len(elevations) < 2:
        return 0.0
    
    total_gain = 0.0
    accumulated_change = 0.0
    
    for i in range(1, len(elevations)):
        change = elevations[i] - elevations[i-1]
        accumulated_change += change
        
        # If we've accumulated enough uphill change, count it
        if accumulated_change >= threshold:
            total_gain += accumulated_change
            accumulated_change = 0.0
        # Reset if we're going downhill significantly
        elif accumulated_change < -threshold:
            accumulated_change = 0.0
    
    # Add any remaining accumulated gain
    if accumulated_change > 0:
        total_gain += accumulated_change
    
    return max(0.0, total_gain)

def process_gpx_for_elevation(gpx, method = "distance_smooth_threshold"):
    """
    Process a GPX object to calculate distance and improved elevation gain.
    """
    import srtm
    
    # Get SRTM elevation data
    elevation_data = srtm.get_data()
    elevation_data.add_elevations = True
    
    # Extract all points with SRTM elevations
    all_points = []
    
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                # Get SRTM elevation
                srtm_elev = elevation_data.get_elevation(point.latitude, point.longitude)
                if srtm_elev is not None:
                    point.elevation = srtm_elev
                    all_points.append((point.latitude, point.longitude, srtm_elev))
    
    # Calculate distance using gpxpy's built-in method (it's quite good)
    distance = sum([t.length_3d() for t in gpx.tracks])
    
    # Calculate elevation gain
    elevation_gain = calculate_elevation_gain(all_points, distance, method=method)

    return distance, elevation_gain