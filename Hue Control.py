"""Streamlit web application for controlling Philips Hue lights.

This application allows users to:
- View and control individual lights, device groups, and rooms.
- Turn lights on/off.
- Adjust brightness for dimmable lights.
- Re-index light information from the Hue Bridge.

Configuration is managed via a .env file for Bridge IP and Hue Application Key,
and JSON files for light structure and UI element ordering.
"""
import streamlit as st
import json
import os
import requests
import urllib3
from dotenv import load_dotenv
import re
from streamlit import fragment

# Set page config as the FIRST Streamlit command
st.set_page_config(
    page_title="Hue Control",
    page_icon="💡",
    layout="wide"
)

# --- Attempt to import the generator function and its config validity ---
try:
    from hue_structure_generator import generate_hue_structure_json, CONFIG_VALID as HUE_GEN_CONFIG_VALID
except ImportError:
    st.error("Failed to import `hue_structure_generator`. Ensure it exists and is in the same directory.")
    def generate_hue_structure_json(verbose=False):
        st.error("Hue structure generator is not available.")
        return None
    HUE_GEN_CONFIG_VALID = False

# Configuration
load_dotenv()
BRIDGE_IP = os.getenv("BRIDGE_IP")
HUE_APP_KEY = os.getenv("HUE_APP_KEY")
STRUCTURE_FILE_PATH = "reference/hue_light_structure.json"
UI_ORDER_FILE_PATH = "reference/ui_order.json"

# --- App and Generator Configuration Check (Initial) ---
APP_CONFIG_VALID = True
if not BRIDGE_IP: st.error("ERROR: BRIDGE_IP not found in .env."); APP_CONFIG_VALID = False
if not HUE_APP_KEY: st.error("ERROR: HUE_APP_KEY not found in .env."); APP_CONFIG_VALID = False

# --- Force Re-index on Every Load/Interaction (if possible) ---
if APP_CONFIG_VALID and HUE_GEN_CONFIG_VALID:
    with st.spinner("Fetching latest light states..."): # Subtle spinner
        generate_hue_structure_json(verbose=False)
else:
    if not APP_CONFIG_VALID:
        st.warning("App .env config invalid. Cannot auto-refresh light states.")
    if not HUE_GEN_CONFIG_VALID:
        st.warning("Hue generator config invalid. Cannot auto-refresh light states.")

# Environment variables and base URL setup (dependent on .env)
if APP_CONFIG_VALID:
    BASE_URL_V2 = f"https://{BRIDGE_IP}/clip/v2"
    HEADERS_V2 = {
        "hue-application-key": HUE_APP_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
else:
    BASE_URL_V2 = None
    HEADERS_V2 = {}

# Data Loading Functions (with caching)
@st.cache_data()
def load_hue_structure(file_path: str):
    """Loads the Hue light structure from a JSON file.

    Args:
        file_path: The path to the JSON file.

    Returns:
        A dictionary containing the Hue light structure, or None if an error occurs.
    """
    try:
        with open(file_path, 'r') as f: return json.load(f)
    except FileNotFoundError: return None
    except json.JSONDecodeError: st.error(f"Error decoding JSON from {file_path}."); return None

@st.cache_data
def get_flat_light_services_map(structure_data: dict) -> dict:
    """Creates a flat map of light service IDs to their details from the structure data.

    Args:
        structure_data: The Hue light structure data.

    Returns:
        A dictionary mapping service_id to service details.
    """
    services_map = {}
    if not structure_data or "rooms" not in structure_data: return {}
    for room in structure_data["rooms"]:
        for group in room.get("device_groups", []):
            for h_device in group.get("hue_devices", []):
                for service in h_device.get("light_services", []): services_map[service["service_id"]] = service
        for s_device in room.get("standalone_devices", []):
            for service in s_device.get("light_services", []): services_map[service["service_id"]] = service
    return services_map

@st.cache_data()
def load_ui_order(file_path: str):
    """Loads the UI element order configuration from a JSON file.

    Args:
        file_path: The path to the JSON file.

    Returns:
        A dictionary containing the UI order configuration, or an empty dict if an error occurs.
    """
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        st.info(f"UI order file (`{file_path}`) not found. Using default order.")
        return {}
    except json.JSONDecodeError:
        st.error(f"Error decoding JSON from {file_path}. Using default order.")
        return {}

@st.cache_data
def get_ordered_room_definitions(structure_data: dict, order_config: dict):
    """Gets room definitions sorted according to the UI order configuration.

    Args:
        structure_data: The Hue light structure data.
        order_config: The UI order configuration.

    Returns:
        A list of room definitions, sorted according to preferred_room_order.
    """
    if not structure_data or "rooms" not in structure_data:
        return []
    rooms_data_internal = structure_data.get("rooms", [])
    preferred_room_order_internal = order_config.get("room_order", [])
    return get_ordered_items(rooms_data_internal, preferred_room_order_internal, "room_name")

# --- Clear Caches After Re-index and Before Use ---
if APP_CONFIG_VALID and HUE_GEN_CONFIG_VALID:
    load_hue_structure.clear()
    get_flat_light_services_map.clear()
    get_ordered_room_definitions.clear()

# Helper Functions for Hue API Interaction
def send_light_payload(service_id: str, payload: dict, action_description: str) -> bool:
    """Sends a PUT request to a specific light service endpoint.

    Args:
        service_id: The ID of the light service to control.
        payload: The JSON payload to send for the light.
        action_description: A description of the action being performed (for error messages).

    Returns:
        True if the command was successful, False otherwise.
    """
    if not APP_CONFIG_VALID: return False
    url = f"{BASE_URL_V2}/resource/light/{service_id}"
    try:
        response = requests.put(url, headers=HEADERS_V2, json=payload, verify=False, timeout=10)
        response.raise_for_status()
        response_data = response.json()
        if "errors" in response_data and response_data["errors"]:
            errors = [err.get('description', 'Unknown API error') for err in response_data["errors"]]
            st.error(f"API Error(s) for {action_description} (light {service_id[-6:]}): {'; '.join(errors)}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        st.error(f"Error for {action_description} (light {service_id[-6:]}): {e}")
        return False

def set_lights_on_off(service_ids: list[str], turn_on: bool):
    """Turns a list of lights on or off.

    Args:
        service_ids: A list of light service IDs to control.
        turn_on: True to turn lights on, False to turn them off.
    """
    if not service_ids: st.warning("No lights provided to turn on/off."); return
    action = "ON" if turn_on else "OFF"; success_count = 0
    total_lights = len(service_ids); my_bar = None
    if total_lights > 1: my_bar = st.progress(0)
    payload = {"on": {"on": turn_on}}; action_description = f"turn {action}"
    for i, service_id in enumerate(service_ids):
        if send_light_payload(service_id, payload, action_description): success_count += 1
        if my_bar: my_bar.progress((i + 1) / total_lights)
    if my_bar: my_bar.empty()
    if success_count == total_lights and total_lights > 0: st.toast(f"Successfully set {success_count} light(s) {action.lower()}.")
    elif total_lights > 0: st.warning(f"Attempted to set {total_lights} light(s) {action.lower()}. {success_count} succeeded.")
    st.session_state.data_dirty = True

def set_lights_brightness(service_ids: list[str], brightness_percent: int, light_details_map: dict):
    """Sets the brightness for a list of dimmable lights.

    Args:
        service_ids: A list of light service IDs to control.
        brightness_percent: The desired brightness percentage (0-100).
        light_details_map: A map of service_id to light details, used to check dimmable support.
    """
    if not service_ids: st.warning("No lights selected for brightness change."); return
    success_count = 0; dimmable_lights_controlled = 0
    total_to_potentially_control = len(service_ids); my_bar = None
    if total_to_potentially_control > 1 : my_bar = st.progress(0)
    payload = {"dimming": {"brightness": float(brightness_percent)}}; action_description = f"set brightness to {brightness_percent}%"
    for i, service_id in enumerate(service_ids):
        light_info = light_details_map.get(service_id)
        if light_info and light_info.get("supports_dimming"):
            dimmable_lights_controlled += 1
            if send_light_payload(service_id, payload, action_description): success_count += 1
        if my_bar: my_bar.progress((i+1)/total_to_potentially_control)
    if my_bar: my_bar.empty()
    if dimmable_lights_controlled == 0 and total_to_potentially_control > 0: st.info("None of the selected lights support brightness adjustment.")
    elif success_count == dimmable_lights_controlled and dimmable_lights_controlled > 0: st.toast(f"Successfully set brightness for {success_count} light(s).")
    elif dimmable_lights_controlled > 0: st.warning(f"Attempted to set brightness for {dimmable_lights_controlled} light(s). {success_count} succeeded.")
    st.session_state.data_dirty = True

# Helper function for ordering
def get_ordered_items(actual_items: list, preferred_order_names: list, name_key: str):
    """Sorts a list of dictionary items based on a preferred order of names.

    Items in preferred_order_names are placed first, in that order.
    Remaining items are sorted alphabetically by the value of their name_key.

    Args:
        actual_items: The list of dictionaries to sort.
        preferred_order_names: A list of names defining the preferred order.
        name_key: The key in each dictionary whose value is used for matching and sorting.

    Returns:
        A new list of sorted items.
    """
    if not preferred_order_names:
        return sorted(actual_items, key=lambda x: x.get(name_key, ""))

    ordered_items_map = {item.get(name_key): item for item in actual_items}
    final_ordered_list = []
    remaining_items_map = ordered_items_map.copy()

    for name in preferred_order_names:
        if name in ordered_items_map:
            final_ordered_list.append(ordered_items_map[name])
            if name in remaining_items_map:
                del remaining_items_map[name]
    
    # Add any items not in preferred_order_names, sorted by name_key
    final_ordered_list.extend(sorted(remaining_items_map.values(), key=lambda x: x.get(name_key, "")))
    return final_ordered_list

@fragment
def render_room_content_fragment(room, room_idx, flat_light_services_map, ui_order_config_data):
    """Renders the UI content for a single room tab within a Streamlit fragment.

    This includes room-level controls (on/off, brightness) and then iterates
    through device groups and standalone devices within the room, rendering
    controls for each.

    Args:
        room: Dictionary containing data for the current room.
        room_idx: Index of the current room (used for unique widget keys).
        flat_light_services_map: A flat map of all light service IDs to their details.
        ui_order_config_data: Configuration for UI element ordering.
    """
    room_all_service_ids = []
    room_dimmable_service_ids = []
    initial_brightness_sum = 0
    lights_on_and_dimmable_count = 0
    temp_room_services = []
    for group in room.get("device_groups", []): 
        for h_device in group.get("hue_devices", []): temp_room_services.extend(h_device.get("light_services", []))
    for s_device in room.get("standalone_devices", []): temp_room_services.extend(s_device.get("light_services", []))
    
    for service_obj in temp_room_services:
        service_id = service_obj["service_id"]
        room_all_service_ids.append(service_id)
        service_detail = flat_light_services_map.get(service_id, {})
        if service_detail.get("supports_dimming"):
            room_dimmable_service_ids.append(service_id)
            if service_detail.get("is_on") and service_detail.get("current_brightness") is not None:
                initial_brightness_sum += service_detail["current_brightness"]
                lights_on_and_dimmable_count += 1
    room_all_service_ids = list(set(room_all_service_ids))
    room_dimmable_service_ids = list(set(room_dimmable_service_ids))
    room_initial_avg_brightness = (initial_brightness_sum / lights_on_and_dimmable_count) if lights_on_and_dimmable_count > 0 else 50.0

    if room_all_service_ids:
        create_on_off_buttons(f"All in {room['room_name']}", room_all_service_ids, f"room_{room_idx}_all")
        if room_dimmable_service_ids:
            brightness_key_room = f"room_{room_idx}_brightness_all"
            def room_brightness_change_callback(r_idx, r_dim_ids, f_l_s_map):
                new_b = st.session_state[f"room_{r_idx}_brightness_all"] 
                set_lights_brightness(r_dim_ids, int(new_b), f_l_s_map)
            st.slider("Room Brightness", min_value=0, max_value=100, value=round(room_initial_avg_brightness),
                        key=brightness_key_room, on_change=room_brightness_change_callback, 
                        args=(room_idx, room_dimmable_service_ids, flat_light_services_map))
    else:
        st.caption(f"No lights found in '{room['room_name']}'.")

    if room_all_service_ids: 
        st.markdown("##### Device Controls within this Room:")
        all_room_devices = []
        device_groups_in_room = room.get("device_groups", [])
        standalone_devices_in_room = room.get("standalone_devices", [])
        
        for dg in device_groups_in_room:
            dg['_ui_sort_name'] = dg.get("group_base_name") 
            dg['_ui_type'] = 'group' 
            all_room_devices.append(dg)
        for sd in standalone_devices_in_room:
            sd['_ui_sort_name'] = sd.get("device_name") 
            sd['_ui_type'] = 'standalone' 
            all_room_devices.append(sd)

        room_specific_device_order = ui_order_config_data.get("device_order_in_room", {}).get(room['room_name'], [])
        ordered_room_devices = get_ordered_items(all_room_devices, room_specific_device_order, "_ui_sort_name")

        for device_item in ordered_room_devices:
            if device_item['_ui_type'] == 'group':
                device_group = device_item
                raw_group_name = device_group.get("group_base_name", "Unnamed Group")
                group_base_name_display = re.sub(r"(\\.?)([A-Z])", r"\\1 \\2", raw_group_name).strip()
                if not group_base_name_display: group_base_name_display = raw_group_name
                
                group_all_service_ids = []
                group_dimmable_service_ids = []
                group_brightness_sum = 0
                group_lights_on_dimmable = 0
                for h_device in device_group.get("hue_devices", []):
                    for service_obj in h_device.get("light_services", []):
                        service_id = service_obj["service_id"]
                        group_all_service_ids.append(service_id)
                        service_detail = flat_light_services_map.get(service_id, {})
                        if service_detail.get("supports_dimming"):
                            group_dimmable_service_ids.append(service_id)
                            if service_detail.get("is_on") and service_detail.get("current_brightness") is not None:
                                group_brightness_sum += service_detail["current_brightness"]
                                group_lights_on_dimmable += 1
                group_all_service_ids = list(set(group_all_service_ids))
                group_dimmable_service_ids = list(set(group_dimmable_service_ids))
                group_initial_avg_brightness = (group_brightness_sum / group_lights_on_dimmable) if group_lights_on_dimmable > 0 else 50.0

                if group_all_service_ids:
                    group_key_suffix = re.sub(r'[^a-zA-Z0-9_]', '', group_base_name_display).lower()
                    st.subheader(f"{group_base_name_display}")
                    create_on_off_buttons(f"Group {group_base_name_display}", group_all_service_ids, f"room_{room_idx}_group_{group_key_suffix}")
                    if group_dimmable_service_ids:
                        brightness_key_group = f"room_{room_idx}_group_{group_key_suffix}_brightness"
                        def group_brightness_callback(b_key, dim_ids, f_l_s_map_cb):
                            new_b_val = st.session_state[b_key]
                            set_lights_brightness(dim_ids, int(new_b_val), f_l_s_map_cb)
                        st.slider(f"Brightness for {group_base_name_display}", min_value=0, max_value=100, value=round(group_initial_avg_brightness),
                                    key=brightness_key_group, 
                                    on_change=group_brightness_callback, 
                                    args=(brightness_key_group, group_dimmable_service_ids, flat_light_services_map))
                    st.markdown("---")

            elif device_item['_ui_type'] == 'standalone':
                standalone_device = device_item
                s_dev_all_service_ids = []
                s_dev_dimmable_service_ids = []
                s_dev_brightness_sum = 0
                s_dev_lights_on_dimmable = 0
                for service_obj in standalone_device.get("light_services", []):
                    service_id = service_obj["service_id"]
                    s_dev_all_service_ids.append(service_id)
                    service_detail = flat_light_services_map.get(service_id, {})
                    if service_detail.get("supports_dimming"):
                        s_dev_dimmable_service_ids.append(service_id)
                        if service_detail.get("is_on") and service_detail.get("current_brightness") is not None:
                            s_dev_brightness_sum += service_detail["current_brightness"]
                            s_dev_lights_on_dimmable += 1
                s_dev_all_service_ids = list(set(s_dev_all_service_ids))
                s_dev_dimmable_service_ids = list(set(s_dev_dimmable_service_ids))
                s_dev_initial_avg_brightness = (s_dev_brightness_sum / s_dev_lights_on_dimmable) if s_dev_lights_on_dimmable > 0 else 50.0

                if s_dev_all_service_ids:
                    s_dev_name = standalone_device['device_name']
                    s_dev_key_suffix = re.sub(r'[^a-zA-Z0-9_]', '', s_dev_name).lower()
                    st.subheader(f"{s_dev_name}")
                    create_on_off_buttons(f"{s_dev_name}", s_dev_all_service_ids, f"room_{room_idx}_sdev_{s_dev_key_suffix}")
                    if s_dev_dimmable_service_ids:
                        brightness_key_sdev = f"room_{room_idx}_sdev_{s_dev_key_suffix}_brightness"
                        def sdev_brightness_callback(b_key, dim_ids, f_l_s_map_cb):
                            new_b_val = st.session_state[b_key]
                            set_lights_brightness(dim_ids, int(new_b_val), f_l_s_map_cb)
                        st.slider(f"Brightness for {s_dev_name}", min_value=0, max_value=100, value=round(s_dev_initial_avg_brightness),
                                    key=brightness_key_sdev, 
                                    on_change=sdev_brightness_callback, 
                                    args=(brightness_key_sdev, s_dev_dimmable_service_ids, flat_light_services_map))
                    st.markdown("---")
            
    st.divider()

# UI Rendering
def create_on_off_buttons(control_label: str, service_ids: list[str], key_prefix: str, use_container_width=True):
    """Creates a pair of ON/OFF buttons in two columns for a set of lights.

    Args:
        control_label: Label used for generating unique keys and displayed text (if applicable).
        service_ids: A list of light service IDs to be controlled by these buttons.
        key_prefix: A prefix for generating unique Streamlit widget keys.
        use_container_width: Whether the buttons should use the full container width.
    """
    if not service_ids: return
    col1, col2 = st.columns(2)
    sanitized_label = re.sub(r'[^a-zA-Z0-9_]', '', control_label.replace(' ', '_')).lower()
    with col1:
        if st.button(f"ON", key=f"on_{key_prefix}_{sanitized_label}", use_container_width=use_container_width):
            set_lights_on_off(service_ids, True)
    with col2:
        if st.button(f"OFF", key=f"off_{key_prefix}_{sanitized_label}", use_container_width=use_container_width):
            set_lights_on_off(service_ids, False)

def get_all_service_ids_from_structure(structure):
    """Extracts all unique light service IDs from the entire Hue structure.

    Args:
        structure: The Hue light structure data.

    Returns:
        A list of all unique light service IDs found in the structure.
    """
    all_ids = []
    if not structure or "rooms" not in structure: return []
    for room in structure["rooms"]:
        for group in room.get("device_groups", []):
            for h_device in group.get("hue_devices", []): all_ids.extend([s["service_id"] for s in h_device.get("light_services", [])])
        for s_device in room.get("standalone_devices", []): all_ids.extend([s["service_id"] for s in s_device.get("light_services", [])])
    return list(set(all_ids))

# Main App
if 'data_dirty' not in st.session_state: st.session_state.data_dirty = False

with st.sidebar:
    st.header("Settings")
    if not HUE_GEN_CONFIG_VALID: st.warning("Generator config invalid. Re-indexing disabled.")
    elif st.button("🔄 Re-index Lights"):
        with st.spinner("Re-indexing lights..."):
            result = generate_hue_structure_json()
            load_hue_structure.clear(); get_flat_light_services_map.clear(); load_ui_order.clear(); st.session_state.data_dirty = True
        if result: st.success("Re-indexing complete! Reloading view...")
        else: st.error("Re-indexing failed. Check console.")
        st.rerun()
    if st.session_state.data_dirty:
        if st.button("🔃 Refresh View"): 
            st.session_state.data_dirty = False; load_hue_structure.clear(); get_flat_light_services_map.clear(); load_ui_order.clear(); st.rerun()

hue_structure = load_hue_structure(STRUCTURE_FILE_PATH)
flat_light_services = get_flat_light_services_map(hue_structure)
ui_order_config = load_ui_order(UI_ORDER_FILE_PATH)

if not APP_CONFIG_VALID: st.error("App .env config invalid. Hue interactions disabled."); st.stop()
if not hue_structure or not flat_light_services:
    st.warning(f"Hue structure file (`{STRUCTURE_FILE_PATH}`) missing, invalid, or empty.")
    if HUE_GEN_CONFIG_VALID: st.markdown("Try **Re-index Lights** in sidebar.")
    else: st.markdown("Generator config also invalid. Fix .env, run generator, or re-index.")
    st.stop()

# Define ordered_rooms and room_names first
ordered_rooms = get_ordered_room_definitions(hue_structure, ui_order_config)
room_names = [room['room_name'] for room in ordered_rooms] if ordered_rooms else []

# Attempt to define tabs as early as possible
# If there are no rooms, room_tabs will be None, and we'll handle that.
room_tabs = st.tabs(room_names) if ordered_rooms else None

# Create a container for elements that used to be above the tabs
main_content_container = st.container()

with main_content_container:
    all_light_service_ids_in_house = get_all_service_ids_from_structure(hue_structure)
    if all_light_service_ids_in_house:
        st.markdown("### 🏠 House Controls")
        create_on_off_buttons("All Lights", all_light_service_ids_in_house, "house_all")
    st.divider()

if ordered_rooms and room_tabs:
    for room_idx, room in enumerate(ordered_rooms):
        with room_tabs[room_idx]:
            # Call the fragment function to render the content for this tab
            render_room_content_fragment(
                room=room, 
                room_idx=room_idx, 
                flat_light_services_map=flat_light_services, 
                ui_order_config_data=ui_order_config
            )
else:
    st.info("No rooms found in the Hue structure. Try re-indexing if you expect to see rooms.")



