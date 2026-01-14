"""Sample Python file for testing Luminary"""

def calculate_total(items):
    """Calculate total price of items"""
    total = 0
    for item in items:
        total += item.price
    return total


def process_user_data(user_id, uc):
    """Process user data"""
    if uc > 100:
        return "Too many users"
    return f"Processing user {user_id}"


def fetch_data(url):
    """Fetch data from URL"""
    # Missing import: import requests
    # Missing error handling
    response = requests.get(url)  # noqa: F401
    return response.json()
