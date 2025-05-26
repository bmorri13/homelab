import os
import sys
import requests
import json
import time
import datetime
from random import randint, choice
from dotenv import load_dotenv
import logging

# load .env into os.environ
load_dotenv()

# configure logging to stdout
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)
logger = logging.getLogger(__name__)

# List of sample actions and animals for more diverse event messages
actions = ["left the barn", "entered the barn", "won the race", "is taking a nap", "is looking for food"]
animals = ["Pony", "Chicken", "Cow", "Sheep", "Goat"]

def send_events_to_splunk(hec_url, token, events):
    headers = {
        'Authorization': f'Splunk {token}',
        'Content-Type': 'application/json'
    }
    data = '\n'.join(json.dumps(event) for event in events)
    return requests.post(hec_url, headers=headers, data=data)

def generate_random_event(sourcetype=None):
    animal       = choice(animals)
    action       = choice(actions)
    event_number = randint(1, 1000)
    temperature  = randint(-10, 40)
    humidity     = randint(20, 80)
    timestamp    = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return {
        "time":       timestamp,
        "sourcetype": sourcetype,
        "event": {
            "animal":       animal,
            "event_number": event_number,
            "action":       action,
            "temperature":  temperature,
            "humidity":     humidity
        },
        "details": {
            "temperature":     f"{temperature}°C",
            "humidity":        f"{humidity}%",
            "action_details": f"Details about {action}."
        }
    }

def main():
    sourcetype = os.getenv("SOURCETYPE", "sample_dev_logs")
    hec_url    = os.getenv("HEC_URL")
    token      = os.getenv("TOKEN")

    if not hec_url or not token:
        logger.error("Missing required environment variables HEC_URL and TOKEN")
        sys.exit(1)

    duration       = 5 * 60   # 5 minutes
    batch_size     = 100
    sleep_interval = 20
    end_time       = time.time() + duration

    while time.time() < end_time:
        events_batch = [generate_random_event(sourcetype=sourcetype) for _ in range(batch_size)]
        response     = send_events_to_splunk(hec_url, token, events_batch)
        logger.info(f"Sent batch of {batch_size} events → Status Code: {response.status_code}")
        time.sleep(sleep_interval)

if __name__ == "__main__":
    main()
