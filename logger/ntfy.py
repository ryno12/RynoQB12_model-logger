"""Push alerts via ntfy.sh. One HTTP POST, no account."""
import requests

from . import config


def send(title: str, message: str, priority: str = "high", tags: str = "football") -> bool:
    if config.DRY_RUN or not config.NTFY_TOPIC:
        print(f"[ntfy dry] {title}\n{message}\n")
        return False
    r = requests.post(
        f"{config.NTFY_SERVER}/{config.NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags},
        timeout=30,
    )
    return r.status_code < 300
