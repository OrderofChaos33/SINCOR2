#!/usr/bin/env python3
"""
Auto Detailers US Discovery Pipeline
Discovers auto detailing businesses across the United States
and exports them to CSV for Google Sheets upload.
"""

import requests
import json
import time
import csv
import os
from datetime import datetime
from pathlib import Path

class AutoDetailersDiscovery:
    """Discover auto detailing businesses across the US."""

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
        self.output_dir = Path(__file__).parent / "output"
        self.output_dir.mkdir(exist_ok=True)

        # Major US cities for discovery
        self.cities = [
            "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX",
            "Phoenix, AZ", "Philadelphia, PA", "San Antonio, TX", "San Diego, CA",
            "Dallas, TX", "San Jose, CA", "Austin, TX", "Jacksonville, FL",
            "Fort Worth, TX", "Columbus, OH", "Charlotte, NC", "San Francisco, CA",
            "Indianapolis, IN", "Seattle, WA", "Denver, CO", "Boston, MA",
            "El Paso, TX", "Nashville, TN", "Detroit, MI", "Oklahoma City, OK",
            "Portland, OR", "Las Vegas, NV", "Memphis, TN", "Louisville, KY",
            "Baltimore, MD", "Milwaukee, WI", "Albuquerque, NM", "Tucson, AZ",
            "Fresno, CA", "Sacramento, CA", "Mesa, AZ", "Kansas City, MO",
            "Atlanta, GA", "Long Beach, CA", "Colorado Springs, CO", "Raleigh, NC",
            "Miami, FL", "Virginia Beach, VA", "Omaha, NE", "Oakland, CA",
            "Minneapolis, MN", "Tulsa, OK", "Arlington, TX", "Tampa, FL"
        ]

        # Auto detailing search queries
        self.queries = [
            "auto detailing",
            "car detailing",
            "mobile car detailing",
            "auto spa",
            "car wash and detailing",
            "luxury car detailing",
            "ceramic coating",
            "paint protection film"
        ]

    def discover_businesses(self):
        """Discover auto detailing businesses in major US cities."""
        all_businesses = []

        print(f"Starting auto detailing business discovery across {len(self.cities)} cities...")

        for city in self.cities:
            print(f"Discovering in {city}...")
            city_businesses = self._discover_in_city(city)
            all_businesses.extend(city_businesses)

            # Rate limiting to avoid API quota issues
            time.sleep(0.2)

        # Remove duplicates based on place_id
        unique_businesses = self._remove_duplicates(all_businesses)

        print(f"Found {len(unique_businesses)} unique auto detailing businesses")

        return unique_businesses

    def _discover_in_city(self, city):
        """Discover businesses in a specific city."""
        businesses = []

        for query in self.queries:
            try:
                # Google Places Text Search API
                url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
                params = {
                    "query": f"{query} in {city}",
                    "key": self.api_key,
                    "type": "car_wash"  # Helps filter results
                }

                response = requests.get(url, params=params, timeout=10)
                data = response.json()

                if data.get("status") == "OK":
                    for place in data.get("results", []):
                        business = self._process_place_data(place, city)
                        if business:
                            businesses.append(business)

                # Rate limiting
                time.sleep(0.1)

            except Exception as e:
                print(f"Error searching {query} in {city}: {e}")
                continue

        return businesses

    def _process_place_data(self, place, city):
        """Process Google Places data into business record."""
        return {
            "name": place.get("name", ""),
            "address": place.get("formatted_address", ""),
            "city": city,
            "rating": place.get("rating", 0.0),
            "review_count": place.get("user_ratings_total", 0),
            "place_id": place.get("place_id", ""),
            "latitude": place.get("geometry", {}).get("location", {}).get("lat", 0),
            "longitude": place.get("geometry", {}).get("location", {}).get("lng", 0),
            "business_status": place.get("business_status", "OPERATIONAL"),
            "discovered_at": datetime.now().isoformat(),
            "industry": "auto_detailing"
        }

    def _remove_duplicates(self, businesses):
        """Remove duplicate businesses based on place_id."""
        seen = set()
        unique = []

        for business in businesses:
            place_id = business.get("place_id")
            if place_id and place_id not in seen:
                seen.add(place_id)
                unique.append(business)

        return unique

    def save_to_csv(self, businesses, filename="auto_detailers_us.csv"):
        """Save businesses to CSV file."""
        output_path = self.output_dir / filename

        if not businesses:
            print("No businesses to save")
            return None

        # Define CSV columns
        fieldnames = [
            "name", "address", "city", "rating", "review_count",
            "place_id", "latitude", "longitude", "business_status",
            "discovered_at", "industry"
        ]

        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(businesses)

        print(f"Saved {len(businesses)} businesses to {output_path}")
        return output_path

def main():
    """Main discovery pipeline."""
    print("Auto Detailers US Discovery Pipeline")
    print("=" * 40)

    discovery = AutoDetailersDiscovery()

    if not discovery.api_key:
        print("ERROR: GOOGLE_PLACES_API_KEY environment variable not set")
        print("Please set your Google Places API key to run this pipeline")
        return 1

    try:
        # Discover businesses
        businesses = discovery.discover_businesses()

        # Save to CSV
        csv_path = discovery.save_to_csv(businesses)

        if csv_path:
            print(f"Discovery complete! CSV saved to: {csv_path}")
            print(f"Ready for upload to Google Sheets")
            return 0
        else:
            print("No businesses discovered")
            return 1

    except Exception as e:
        print(f"Pipeline failed: {e}")
        return 1

if __name__ == "__main__":
    exit(main())