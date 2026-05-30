import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# track active huddles {call_id: set(participant_ids)}
active_huddles = {}

@app.event("app_mention")
def handle_mention(event, say):
    say("Meridian is live 👋")

@app.event("user_huddle_changed")
def handle_huddle(event, client):
    user = event["user"]
    profile = user.get("profile", {})
    huddle_state = profile.get("huddle_state")
    call_id = profile.get("huddle_state_call_id")
    user_id = user.get("id")

    if huddle_state == "in_a_huddle" and call_id:
        if call_id not in active_huddles:
            active_huddles[call_id] = set()
        active_huddles[call_id].add(user_id)
        print(f"User {user_id} joined huddle {call_id}")

    elif huddle_state == "default_unset":
        # call_id is gone on leave, so find by user_id
        for cid in list(active_huddles.keys()):
            if user_id in active_huddles[cid]:
                active_huddles[cid].discard(user_id)
                print(f"User {user_id} left huddle {cid}")

                if len(active_huddles[cid]) == 0:
                    print(f"Huddle {cid} ended — triggering Meridian")
                    del active_huddles[cid]
                    trigger_meridian(cid, client)
                break

@app.event("message")
def handle_message(event, logger):
    logger.info(event)

def trigger_meridian(call_id, client):
    print(f"Meridian triggered for huddle {call_id}")

    import anthropic

    test_query = "onboarding problem struggling"

    # 1. pull customer signals
    try:
        results = client.search_messages(
            token=os.environ["SLACK_USER_TOKEN"],
            query=test_query,
            count=5
        )
        messages = results.get("messages", {}).get("matches", [])
        print(f"Search found {len(messages)} relevant messages")
    except Exception as e:
        print(f"Search error: {e}")
        messages = []

    # 2. format signals for Claude
    signal_text = "\n".join([
        f"- [{m.get('channel', {}).get('name')}] {m.get('text', '')}"
        for m in messages
    ]) or "No customer signals found."

    # 3. fake huddle transcript for now (Recall.ai would provide this)
    fake_transcript = """
    Rachel: ok so we need to decide whether to prioritise the onboarding redesign or the export bug
    Rachel: i think onboarding is more urgent based on what customers are saying
    Rachel: let's go with onboarding first, i'll own that, targeting end of next week
    """

    # 4. ask Claude to synthesise it
    ai_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""You are Meridian, an AI agent that turns Slack huddles into structured decisions.

Huddle transcript:
{fake_transcript}

Customer signals from Slack:
{signal_text}

Extract and return:
1. Key decisions made
2. Action items with owners
3. Supporting customer evidence

Format as clean markdown suitable for a Slack canvas."""

    response = ai_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    canvas_content = response.content[0].text
    print("Claude output:\n", canvas_content)

    # 5. create the canvas in #general (or whatever channel you want)
    # first get the channel ID for #general
    channels = client.conversations_list(types="public_channel")
    channel_id = next(
        (c["id"] for c in channels["channels"] if c["name"] in ["general", "meridian-dev", "all-meridian-dev"]),
        None
    )

    if channel_id:
        canvas = client.api_call(
            "canvases.create",
            json={
                "title": f"Meridian — Huddle Summary",
                "document_content": {
                    "type": "markdown",
                    "markdown": canvas_content
                }
            }
        )
        canvas_id = canvas.get("canvas_id")
        print(f"Canvas created: {canvas_id}")

        # get team info for correct canvas URL
        team_info = client.team_info()
        team_id = team_info["team"]["id"]
        domain = team_info["team"]["domain"]

        client.chat_postMessage(
            channel=channel_id,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"📋 *Meridian — Huddle Summary ready*\n<https://{domain}.slack.com/docs/{team_id}/{canvas_id}|View canvas>"
                    }
                }
            ],
            text="Meridian huddle summary ready"
        )
        print("Posted to channel!")
    else:
        print("Couldn't find #general channel")

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()