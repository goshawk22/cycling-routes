import os
import re
import gpxpy
import srtm
from elevation_utils import process_gpx_for_elevation

chosen_method = "moving_average_threshold"

def old_elevation_calculation(gpx):
    """The original elevation calculation method from your app."""
    elevation_data = srtm.get_data()
    elevation_data.add_elevations = True
    
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                srtm_elev = elevation_data.get_elevation(point.latitude, point.longitude)
                if srtm_elev is not None:
                    point.elevation = srtm_elev

    elevation_gain = 0
    for track in gpx.tracks:
        for segment in track.segments:
            uphill, _ = segment.get_uphill_downhill()
            elevation_gain += uphill
    
    return elevation_gain

def compare_methods():
    """Compare old vs new elevation calculation methods."""
    upload_dir = 'uploads'
    
    if not os.path.exists(upload_dir):
        print(f"Upload directory '{upload_dir}' not found!")
        return
    
    gpx_files = [f for f in os.listdir(upload_dir) if f.endswith('.gpx')]
    
    if not gpx_files:
        print(f"No GPX files found in '{upload_dir}'!")
        return

    print(f"=== ELEVATION CALCULATION COMPARISON (Strava vs {chosen_method}) ===\n")
    
    total_strava = 0
    total_new = 0
    count = 0

    # Try to load Strava gains from file (ordered by route id)
    strava_gains = []
    strava_file = 'strava-gains.txt'
    if os.path.exists(strava_file):
        with open(strava_file, 'r') as sf:
            for line in sf:
                m = re.search(r"(\d+)\s*m", line)
                if m:
                    strava_gains.append(int(m.group(1)))
    # If file not found or parsing failed, strava_gains may be empty and we'll show N/A
    
    for gpx_file in sorted(gpx_files):
        filepath = os.path.join(upload_dir, gpx_file)
        try:
            with open(filepath, 'r') as f:
                gpx = gpxpy.parse(f)
            # Compare Strava vs both methods with tuned params
            ma_params = {'window_size': 3, 'base_threshold': 1.5, 'multiplier': 1.5, 'min_segment_distance': 3.0, 'max_gradient': 0.30}
            dist_params = {'smooth_distance': 25.0, 'threshold': 1.5}

            dist, ma_gain = process_gpx_for_elevation(gpx, 'moving_average_threshold', params=ma_params)
            _, dist_gain = process_gpx_for_elevation(gpx, 'distance_smooth_threshold', params=dist_params)

            # Map GPX filename prefix (e.g. '1-...gpx') to strava list index
            strava_val = None
            m = re.match(r"^(\d+)-", gpx_file)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(strava_gains):
                    strava_val = strava_gains[idx]

            chosen_method_result = 0
            if ma_gain / dist * 1000 > 11:
                chosen_method_result = dist_gain
            else:
                chosen_method_result = ma_gain
            
            # Compute difference (method - strava) for both methods
            diff = None
            diff_pct = None
            if strava_val is not None:
                diff_ma = ma_gain - strava_val
                diff_pct_ma = (diff_ma / strava_val) * 100 if strava_val != 0 else None
                diff_dist = dist_gain - strava_val
                diff_pct_dist = (diff_dist / strava_val) * 100 if strava_val != 0 else None
                diff_chosen = chosen_method_result - strava_val
                diff_pct_chosen = (diff_chosen / strava_val) * 100 if strava_val != 0 else None

            route_name = gpx_file[:30]
            if strava_val is None:
                strava_display = 'N/A'
                ma_display = 'N/A'
                dist_display = 'N/A'
            else:
                strava_display = f"{strava_val:.0f}"
                ma_display = f"{ma_gain:<6.0f} {diff_ma:+.0f} ({diff_pct_ma:+.1f}%)"
                dist_display = f"{dist_gain:<6.0f} {diff_dist:+.0f} ({diff_pct_dist:+.1f}%)"
                chosen_display = f"{chosen_method_result:<6.0f} {diff_chosen:+.0f} ({diff_pct_chosen:+.1f}%)"

            print(f"{route_name:<30} {strava_display:<10} MA: {ma_display:<24} DW: {dist_display:<24} Chosen: {chosen_display}")

            if strava_val is not None:
                total_strava += strava_val
                total_new += dist_gain
                count += 1
        
        except Exception as e:
            print(f"{gpx_file:<25} ERROR: {str(e)}")
    
    if count > 0:
        avg_strava = total_strava / count
        avg_new = total_new / count
        diff_pct = ((avg_new - avg_strava) / avg_strava) * 100 if avg_strava != 0 else 0.0

        print("-" * 80)
        print(f"{ 'AVERAGES:':<30} {avg_strava:<10.0f} {avg_new:<12.0f}")
        print(f"\nSummary:")
        print(f"  Difference: {diff_pct:+.1f}%")
    else:
        print("\nNo matching Strava entries found (check 'strava-gains.txt' ordering and filenames).")
    
if __name__ == "__main__":
    compare_methods()