import requests
import json
import os
import re
from dotenv import load_dotenv
import urllib3

# Load environment variables from .env file
load_dotenv()

# Disable InsecureRequestWarning for self-signed Hue Bridge certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
BRIDGE_IP = os.getenv("BRIDGE_IP")
HUE_APP_KEY = os.getenv("HUE_APP_KEY")
DEFAULT_OUTPUT_FILE = "reference/hue_light_structure.json"
UI_ORDER_FILE_PATH_DEFAULT = "reference/ui_order.json"

CONFIG_VALID = True
if not BRIDGE_IP:
    print("ERROR: BRIDGE_IP not found in environment variables. Ensure it's set in your .env file.")
    CONFIG_VALID = False
if not HUE_APP_KEY:
    print("ERROR: HUE_APP_KEY not found in environment variables. Ensure it's set in your .env file.")
    CONFIG_VALID = False

BASE_URL_V2 = f"https://{BRIDGE_IP}/clip/v2" if CONFIG_VALID else None
HEADERS_V2 = {
    "hue-application-key": HUE_APP_KEY,
    "Accept": "application/json"
} if CONFIG_VALID else {}

# --- Helper Functions ---

def _get_hue_resources(resource_type: str):
    """Fetches resources of a given type from the Hue Bridge."""
    if not CONFIG_VALID:
        print("Configuration invalid. Cannot fetch resources.")
        return None
    
    url = f"{BASE_URL_V2}/resource/{resource_type}"
    # print(f"DEBUG: Fetching {url}") # Uncomment for debugging API calls
    try:
        response = requests.get(url, headers=HEADERS_V2, verify=False, timeout=10)
        response.raise_for_status()
        data = response.json()
        # print(f"DEBUG: Response for {resource_type}: {json.dumps(data, indent=2)[:500]}...") # Uncomment for debugging
        if "errors" in data and data["errors"]:
            print(f"API Error(s) when fetching {resource_type}:")
            for error in data["errors"]:
                print(f"  - Description: {error.get('description', 'No description')}")
            return None
        return data.get("data", [])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {resource_type}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error fetching {resource_type}: {e}")
        return None

def _normalize_device_name(name: str) -> str:
    """
    Normalizes a device name by removing trailing numbers and whitespace,
    and then removing all internal whitespace.
    Example: "Bed Boob 1" -> "BedBoob", "Office Lamp" -> "OfficeLamp"
    """
    if not name:
        return "UnnamedDevice"
    # Remove trailing digits and any space before them
    base_name = re.sub(r'\s*\d+$', '', name)
    # Remove all remaining whitespace
    return re.sub(r'\s+', '', base_name)

def generate_hue_structure_json(output_file_path: str = DEFAULT_OUTPUT_FILE, verbose: bool = True) -> dict | None:
    """
    Fetches data from Hue Bridge, builds a hierarchical structure including light capabilities,
    saves it to a JSON file, and returns the structure.
    """
    if not CONFIG_VALID:
        # This is a critical config error, always print
        print("Cannot generate structure: Essential configuration (BRIDGE_IP or HUE_APP_KEY) is missing.")
        return None

    if verbose:
        print("Fetching data from Hue Bridge...")
    devices_raw = _get_hue_resources("device")
    light_services_raw = _get_hue_resources("light") # These are light services with full details
    rooms_raw = _get_hue_resources("room")

    if not all([devices_raw, light_services_raw, rooms_raw]):
        # This indicates a failure to fetch, should generally be visible or logged
        print("Failed to fetch one or more essential resource types. Aborting structure generation.")
        return None

    if verbose:
        print("Processing data and building structure...")

    # 1. Create a map of device IDs to their names and normalized names
    device_details_map = {}
    for dev in devices_raw:
        dev_id = dev.get("id")
        dev_name = dev.get("metadata", {}).get("name", f"Unnamed Device {dev_id[:6]}")
        if dev_id:
            device_details_map[dev_id] = {
                "name": dev_name,
                "normalized_name": _normalize_device_name(dev_name),
                "id": dev_id
            }

    # 2. Map light services to their owner Hue devices
    # Each light service has an owner (a Hue device)
    hue_device_to_light_services = {}
    for service in light_services_raw:
        owner_id = service.get("owner", {}).get("rid")
        service_id = service.get("id")
        # Use metadata name for service if available, else default
        service_name_meta = service.get("metadata", {}).get("name")

        if owner_id and service_id:
            owner_device_name = device_details_map.get(owner_id, {}).get("name", "Unknown Owner")
            
            # Construct a more descriptive service name if its own metadata.name is generic or missing
            final_service_name = service_name_meta
            if not final_service_name or final_service_name == owner_device_name: # often light service is named same as device
                 final_service_name = f"{owner_device_name} Light" # Append "Light" for clarity

            # Extract capabilities
            supports_dimming = "dimming" in service
            current_brightness = service.get("dimming", {}).get("brightness") if supports_dimming else None
            supports_color = "color" in service
            supports_color_temperature = "color_temperature" in service
            is_on = service.get("on", {}).get("on", False) # Get current on state

            if owner_id not in hue_device_to_light_services:
                hue_device_to_light_services[owner_id] = []
            hue_device_to_light_services[owner_id].append({
                "service_id": service_id,
                "service_name": final_service_name,
                "original_metadata_name": service_name_meta, # Keep for reference
                "is_on": is_on,
                "supports_dimming": supports_dimming,
                "current_brightness": current_brightness if is_on and supports_dimming else None, # Only store if on and supports
                "supports_color": supports_color,
                "supports_color_temperature": supports_color_temperature
            })

    # 3. Build the final structure based on rooms
    house_structure = {
        "house_name": f"Hue Setup on {BRIDGE_IP}",
        "rooms": []
    }

    for room in rooms_raw:
        room_id = room.get("id")
        room_name = room.get("metadata", {}).get("name", f"Unnamed Room {room_id[:6]}")
        
        current_room_data = {
            "room_name": room_name,
            "room_id": room_id,
            "device_groups": {}, # Keyed by normalized_name, e.g., "BedBoob"
            "standalone_devices": [] # For devices that aren't part of a numbered group
        }

        # Collect all Hue device IDs associated with this room
        room_hue_device_ids = []
        for child_ref in room.get("children", []):
            if child_ref.get("rtype") == "device":
                room_hue_device_ids.append(child_ref.get("rid"))
        
        # Temporary dict to group Hue devices by their normalized name within this room
        devices_by_normalized_name_in_room = {}
        for dev_id in room_hue_device_ids:
            detail = device_details_map.get(dev_id)
            if detail:
                norm_name = detail["normalized_name"]
                if norm_name not in devices_by_normalized_name_in_room:
                    devices_by_normalized_name_in_room[norm_name] = []
                
                devices_by_normalized_name_in_room[norm_name].append({
                    "device_name": detail["name"],
                    "device_id": detail["id"],
                    "light_services": hue_device_to_light_services.get(dev_id, [])
                })

        # Populate device_groups and standalone_devices for the current room
        for norm_name, hue_devices_list in devices_by_normalized_name_in_room.items():
            if len(hue_devices_list) > 1: # It's a group like "Bed Boob 1", "Bed Boob 2"
                current_room_data["device_groups"][norm_name] = {
                     "group_base_name": norm_name, # e.g. "BedBoob"
                     "hue_devices": sorted(hue_devices_list, key=lambda x: x["device_name"])
                }
            elif len(hue_devices_list) == 1: # It's a standalone device in this context
                current_room_data["standalone_devices"].append(hue_devices_list[0])
        
        # Sort standalone devices by name for consistency
        current_room_data["standalone_devices"].sort(key=lambda x: x["device_name"])
        
        # Convert device_groups dict to a list, sorted by group_base_name
        current_room_data["device_groups"] = sorted(
            list(current_room_data["device_groups"].values()),
            key=lambda x: x["group_base_name"]
        )

        house_structure["rooms"].append(current_room_data)

    # Sort rooms by name
    house_structure["rooms"].sort(key=lambda x: x["room_name"])

    # --- Create a default UI order file if it doesn_t exist ---
    if not os.path.exists(UI_ORDER_FILE_PATH_DEFAULT):
        if verbose:
            print(f"INFO: UI order file not found at {UI_ORDER_FILE_PATH_DEFAULT}. Creating a default one.")
        default_ui_order = {
            "room_order": [room["room_name"] for room in house_structure["rooms"]],
            "device_order_in_room": {}
        }
        for room_data in house_structure["rooms"]:
            room_name = room_data["room_name"]
            device_names_in_room = []
            # Collect group base names (already sorted by generator)
            for group in room_data.get("device_groups", []):
                device_names_in_room.append(group["group_base_name"])
            # Collect standalone device names (already sorted by generator)
            for device in room_data.get("standalone_devices", []):
                device_names_in_room.append(device["device_name"])
            # The generator itself sorts these lists, so direct append is fine for default order
            default_ui_order["device_order_in_room"][room_name] = device_names_in_room
        
        try:
            os.makedirs(os.path.dirname(UI_ORDER_FILE_PATH_DEFAULT), exist_ok=True)
            with open(UI_ORDER_FILE_PATH_DEFAULT, 'w') as f_order:
                json.dump(default_ui_order, f_order, indent=2)
            if verbose:
                print(f"Successfully created default UI order file at: {UI_ORDER_FILE_PATH_DEFAULT}")
        except IOError as e:
            # Error during file operation, should generally be visible
            print(f"Error creating default UI order file {UI_ORDER_FILE_PATH_DEFAULT}: {e}")

    # Save to file
    try:
        # Ensure the 'reference' directory exists
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        with open(output_file_path, 'w') as f:
            json.dump(house_structure, f, indent=2)
        if verbose:
            print(f"Successfully generated and saved Hue structure (with capabilities) to: {output_file_path}")
    except IOError as e:
        # Critical error saving the main file
        print(f"Error saving Hue structure to file {output_file_path}: {e}")
        return None # Indicate failure to save, though structure might be in memory

    return house_structure

if __name__ == "__main__":
    print("--- Hue Light Structure Generator (with Capabilities) ---")
    if CONFIG_VALID:
        generated_structure = generate_hue_structure_json() # Will use verbose=True by default
        if generated_structure:
            # print("\nGenerated Structure (first room sample):")
            # if generated_structure["rooms"]:
            #     print(json.dumps(generated_structure["rooms"][0], indent=2))
            # else:
            #     print("No rooms found in the structure.")
            print("\nScript finished.")
        else:
            print("\nStructure generation failed. Check errors above.")
    else:
        print("Script cannot run due to missing configuration in .env file.") 