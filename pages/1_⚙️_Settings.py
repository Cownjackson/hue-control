import streamlit as st
import json
import os

# Define the path to the UI order file, relative to the workspace root
UI_ORDER_FILE_PATH = "reference/ui_order.json"

@st.cache_data(ttl=5)
def load_ui_order(file_path: str):
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        # This page is for editing, so the file should ideally exist.
        # If not, the generator should create it.
        st.error(f"UI order file (`{file_path}`) not found. Please run the main app or generator first to create a default.")
        return {} # Return empty dict to prevent further errors, though UI might be limited
    except json.JSONDecodeError:
        st.error(f"Error decoding JSON from {file_path}. Check its format.")
        return {}

def show_settings_page():
    st.title("⚙️ UI Order Settings")

    st.info("Here you can define the order in which rooms and devices appear in the main application. "
            "Names must exactly match those in your Hue setup (room names, device group base names, or standalone device names)."
            "After saving, restart the app to see changes.")

    ui_order_data = load_ui_order(UI_ORDER_FILE_PATH)
    
    if not ui_order_data and not os.path.exists(UI_ORDER_FILE_PATH):
        st.warning(f"The UI order file (`{UI_ORDER_FILE_PATH}`) does not exist. "
                   f"Please run the `hue_structure_generator.py` script or the main app page at least once "
                   f"to generate an initial structure and a default order file.")
        return # Stop rendering the rest of the page if the base file is missing

    current_room_order_str = "\n".join(ui_order_data.get("room_order", []))
    device_orders_str_map = { 
        room: "\n".join(devices)
        for room, devices in ui_order_data.get("device_order_in_room", {}).items()
    }

    st.header("Room Order")
    st.caption("Define the order of rooms. One room name per line.")
    new_room_order_str = st.text_area(
        "Edit Room Order:", 
        value=current_room_order_str, 
        height=150, 
        key="settings_room_order_text_area",
        label_visibility="collapsed"
    )

    st.header("Device Order within Rooms")
    st.caption("Define the order of devices/groups within each room. One name per line.")
    
    temp_new_room_order_list = [r.strip() for r in new_room_order_str.split('\n') if r.strip()]
    
    # Determine the list of rooms for the selectbox
    if temp_new_room_order_list:
        room_selection_list = temp_new_room_order_list
    elif "room_order" in ui_order_data and ui_order_data["room_order"]:
        room_selection_list = ui_order_data["room_order"] # Fallback to loaded if text area cleared
    else: # No rooms defined anywhere
        room_selection_list = []
        st.info("No rooms available to configure. Define rooms in the 'Room Order' section above first.")


    if room_selection_list:
        selected_room_for_device_edit = st.selectbox(
            "Select Room to Edit Device Order:", 
            options=room_selection_list,
            key="settings_selected_room_device_edit",
            label_visibility="collapsed"
        )

        if selected_room_for_device_edit:
            # Ensure key for text_area is robust if room name contains special characters
            s_room_key_part = "".join(filter(str.isalnum, selected_room_for_device_edit))
            
            current_device_order_for_room_str = device_orders_str_map.get(selected_room_for_device_edit, "")
            
            st.text_area(
                f"Device Order for '{selected_room_for_device_edit}':",
                value=current_device_order_for_room_str,
                height=200,
                key=f"settings_device_order_text_area_{s_room_key_part}"
            )
    # else already handled by st.info above if room_selection_list is empty

    st.divider()

    if st.button("Save UI Order", key="settings_save_ui_order_button"):
        updated_room_order_list = [r.strip() for r in new_room_order_str.split('\n') if r.strip()]
        
        new_device_order_config = {}
        
        # Iterate through rooms that are either in the new room order or had existing device configurations
        all_rooms_to_consider_for_device_order = list(dict.fromkeys(
             updated_room_order_list + list(device_orders_str_map.keys())
        ))

        for room_name in all_rooms_to_consider_for_device_order:
            room_key_part = "".join(filter(str.isalnum, room_name))
            # Check if this room was the one selected and thus its text_area is in session_state
            if room_name == selected_room_for_device_edit and f"settings_device_order_text_area_{room_key_part}" in st.session_state:
                devices_str_for_this_room = st.session_state[f"settings_device_order_text_area_{room_key_part}"]
            else: # Otherwise, use the existing data from the loaded map (or empty if it's a new room)
                devices_str_for_this_room = device_orders_str_map.get(room_name, "")
            
            new_device_order_config[room_name] = [d.strip() for d in devices_str_for_this_room.split('\n') if d.strip()]

        updated_ui_order = {
            "room_order": updated_room_order_list,
            "device_order_in_room": new_device_order_config
        }
        try:
            os.makedirs(os.path.dirname(UI_ORDER_FILE_PATH), exist_ok=True) # Ensure 'reference' dir exists
            with open(UI_ORDER_FILE_PATH, 'w') as f:
                json.dump(updated_ui_order, f, indent=2)
            st.success(f"UI order saved to {UI_ORDER_FILE_PATH}!")
            st.markdown("**Important:** Go to the main 'test_app' page and click 'Refresh View' or reload the application to see your ordering changes reflected in the tabs and device lists.")
            
            load_ui_order.clear() # Clear cache for this page
            if 'data_dirty' in st.session_state: # Signal main app to enable refresh button
                st.session_state.data_dirty = True
                
        except IOError as e:
            st.error(f"Error saving UI order to {UI_ORDER_FILE_PATH}: {e}")

if __name__ == "__main__":
    show_settings_page() 