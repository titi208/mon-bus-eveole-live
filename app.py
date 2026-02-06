from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import pandas as pd
from datetime import datetime
import time
import os
import pickle
import pytz  # NOUVEAU : Pour gérer l'heure de Paris

app = Flask(__name__, template_folder='templates')
CORS(app)

DATA_DIR = "data"
CACHE_FILE = "data/prod_data_v16.pkl"

print("🚀 Démarrage Serveur (Timezone Fix)...")

def load_data():
    # Nettoyage cache au démarrage pour éviter les conflits
    if os.path.exists(CACHE_FILE):
        try: os.remove(CACHE_FILE)
        except: pass
    
    try:
        # Chargement des fichiers
        stops = pd.read_csv(f"{DATA_DIR}/stops.txt", dtype={'stop_id': str}).fillna("")[['stop_id', 'stop_name', 'stop_lat', 'stop_lon']]
        trips = pd.read_csv(f"{DATA_DIR}/trips.txt", dtype={'route_id': str, 'service_id': str, 'trip_id': str})[['route_id', 'service_id', 'trip_id', 'trip_headsign']]
        routes = pd.read_csv(f"{DATA_DIR}/routes.txt", dtype={'route_id': str})[['route_id', 'route_short_name', 'route_long_name', 'route_color', 'route_text_color']]
        stop_times = pd.read_csv(f"{DATA_DIR}/stop_times.txt", dtype={'stop_id': str, 'trip_id': str})[['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence']]

        # --- FILTRE CALENDRIER ---
        try:
            calendar = pd.read_csv(f"{DATA_DIR}/calendar.txt", dtype={'service_id': str})
            
            # On prend la date actuelle à PARIS pour le filtrage
            tz_paris = pytz.timezone('Europe/Paris')
            now_paris = datetime.now(tz_paris)
            
            today_int = int(now_paris.strftime('%Y%m%d'))
            day_name = now_paris.strftime('%A').lower()
            
            days_map = {'monday': 'monday', 'tuesday': 'tuesday', 'wednesday': 'wednesday', 'thursday': 'thursday', 'friday': 'friday', 'saturday': 'saturday', 'sunday': 'sunday'}
            col_day = days_map.get(day_name)

            active_services = calendar[
                (calendar['start_date'] <= today_int) & 
                (calendar['end_date'] >= today_int) & 
                (calendar[col_day] == 1)
            ]['service_id']
            
            trips = trips[trips['service_id'].isin(active_services)]
            stop_times = stop_times[stop_times['trip_id'].isin(trips['trip_id'])]
            print(f"✅ Calendrier Paris appliqué : {day_name} {today_int}")

        except Exception as e:
            print(f"⚠️ Erreur calendrier : {e}")

        # Conversion horaires
        def t2s(t):
            try: h,m,s=map(int, t.split(':')); return h*3600+m*60+s
            except: return 0
        
        stop_times['arrival_sec'] = stop_times['arrival_time'].apply(t2s)
        stop_times['departure_sec'] = stop_times['departure_time'].apply(t2s)
        stop_times = stop_times.sort_values(by=['trip_id', 'stop_sequence'])

        # Mapping
        merged = stop_times.merge(trips, on='trip_id').merge(routes, on='route_id')
        stop_to_lines = merged.groupby('stop_id')['route_short_name'].unique().apply(list).to_dict()
        stops['lines'] = stops['stop_id'].map(stop_to_lines)
        
        data = (stops, trips, routes, stop_times)
        try:
            with open(CACHE_FILE, 'wb') as f: pickle.dump(data, f)
        except: pass
        return data

    except Exception as e:
        return None, None, None, None

stops, trips, routes, stop_times = load_data()

# --- CORRECTION CRUCIALE : HEURE DE PARIS ---
def get_paris_time_seconds():
    # Définit le fuseau horaire
    tz = pytz.timezone('Europe/Paris')
    # Récupère l'heure actuelle dans ce fuseau
    now = datetime.now(tz)
    return now.hour * 3600 + now.minute * 60 + now.second

@app.route('/')
def home(): return render_template('index.html')

@app.route('/api/lines')
def get_lines(): return jsonify(routes.to_dict(orient='records'))

@app.route('/api/stops')
def get_stops():
    unique_stops = stops.groupby('stop_name').first().reset_index()
    unique_stops['lines'] = unique_stops['lines'].apply(lambda x: x if isinstance(x, list) else [])
    return jsonify(unique_stops.to_dict(orient='records'))

@app.route('/api/bus-positions')
def get_positions():
    curr = get_paris_time_seconds()
    # Fenêtre +/- 30 min
    active = stop_times[(stop_times['arrival_sec'] > curr - 1800) & (stop_times['departure_sec'] < curr + 1800)]
    
    if active.empty: return jsonify([])

    merged = active.merge(trips, on='trip_id').merge(routes, on='route_id').merge(stops, on='stop_id')
    buses = []
    
    for trip_id, group in merged.groupby('trip_id'):
        group = group.sort_values('stop_sequence')
        prev = group[group['departure_sec'] <= curr].tail(1)
        nxt = group[group['arrival_sec'] >= curr].head(1)
        
        if not prev.empty and not nxt.empty:
            p = prev.iloc[0]; n = nxt.iloc[0]
            if p['stop_id'] == n['stop_id']: continue

            total = int(n['arrival_sec']) - int(p['departure_sec'])
            elapsed = curr - int(p['departure_sec'])
            pct = elapsed / total if total > 0 else 0
            
            buses.append({
                "id": str(trip_id),
                "line": str(p['route_short_name']),
                "dest": str(p['trip_headsign']),
                "color": str(p['route_color']),
                "text_color": str(p['route_text_color']),
                "p_lat": float(p['stop_lat']), "p_lon": float(p['stop_lon']),
                "n_lat": float(n['stop_lat']), "n_lon": float(n['stop_lon']),
                "pct": float(pct)
            })
    return jsonify(buses)

@app.route('/api/trip-details')
def get_details():
    tid = request.args.get('trip_id')
    if not tid: return jsonify([])
    try:
        t = stop_times[stop_times['trip_id'] == str(tid)].sort_values('stop_sequence')
        final = t.merge(stops, on='stop_id')
        return jsonify(final[['stop_name', 'arrival_time', 'stop_lat', 'stop_lon', 'arrival_sec']].to_dict(orient='records'))
    except: return jsonify([])

@app.route('/api/trip-path')
def get_trip_path():
    tid = request.args.get('trip_id')
    if not tid: return jsonify([])
    try:
        t = stop_times[stop_times['trip_id'] == str(tid)].sort_values('stop_sequence')
        path_data = t.merge(stops, on='stop_id')
        return jsonify(path_data[['stop_lat', 'stop_lon']].values.tolist())
    except: return jsonify([])

@app.route('/api/stop-schedule')
def get_stop_schedule():
    stop_name = request.args.get('stop_name')
    if not stop_name: return jsonify([])
    target_ids = stops[stops['stop_name'] == stop_name]['stop_id'].unique()
    curr = get_paris_time_seconds()
    future = stop_times[(stop_times['stop_id'].isin(target_ids)) & (stop_times['departure_sec'] > curr)].sort_values('departure_sec').head(10)
    if future.empty: return jsonify([])
    result = future.merge(trips, on='trip_id').merge(routes, on='route_id')
    schedule = []
    for _, row in result.iterrows():
        schedule.append({
            "line": str(row['route_short_name']),
            "dest": str(row['trip_headsign']),
            "time": time.strftime('%H:%M', time.gmtime(row['departure_sec'])),
            "wait": int((row['departure_sec'] - curr) / 60),
            "color": str(row['route_color']),
            "text_color": str(row['route_text_color'])
        })
    return jsonify(schedule)

@app.route('/api/route')
def find_route():
    start = request.args.get('start', '').lower()
    end = request.args.get('end', '').lower()
    if not start or not end: return jsonify([])
    
    s_ids = stops[stops['stop_name'].str.lower().str.contains(start, na=False)]['stop_id'].unique()
    e_ids = stops[stops['stop_name'].str.lower().str.contains(end, na=False)]['stop_id'].unique()
    if len(s_ids) == 0 or len(e_ids) == 0: return jsonify([])

    t_start = stop_times[stop_times['stop_id'].isin(s_ids)][['trip_id', 'stop_sequence', 'departure_sec']]
    t_end = stop_times[stop_times['stop_id'].isin(e_ids)][['trip_id', 'stop_sequence', 'arrival_sec']]
    
    common = t_start.merge(t_end, on='trip_id', suffixes=('_start', '_end'))
    valid = common[common['stop_sequence_start'] < common['stop_sequence_end']]
    
    # Correction : On utilise l'heure de Paris pour filtrer
    valid = valid[valid['departure_sec'] > get_paris_time_seconds()]
    
    valid = valid.drop_duplicates(subset=['trip_id'])
    valid = valid.sort_values('departure_sec').head(10)
    
    res = []
    for _, row in valid.iterrows():
        tid = str(row['trip_id'])
        rid = trips[trips['trip_id'] == tid].iloc[0]['route_id']
        rinfo = routes[routes['route_id'] == rid].iloc[0]
        res.append({
            "trip_id": tid,
            "line": str(rinfo['route_short_name']),
            "dep": time.strftime('%H:%M', time.gmtime(row['departure_sec'])),
            "arr": time.strftime('%H:%M', time.gmtime(row['arrival_sec'])),
            "color": str(rinfo['route_color'])
        })
    return jsonify(res)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)