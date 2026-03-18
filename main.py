import os
import discord
from discord import app_commands, ui # New import for Slash Commands
from discord.ext import commands
from dotenv import load_dotenv
from supabase import create_client, Client
from flask import Flask
from threading import Thread
import re

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    # Koyeb specifically looks for port 8000 by default
    app.run(host='0.0.0.0', port=8000)

def keep_alive():
    t = Thread(target=run)
    t.start()

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

user_schedules = {}

# load all data when the bot starts
def load_data():
    global user_schedules
    try:
        # Fetch all rows from the 'schedules' table
        response = supabase.table("schedules").select("*").execute()

        # Convert the database rows back into our Python dictionary
        user_schedules = {int(row['user_id']): row['schedule_data'] for row in response.data}
        print(f"✅ Successfully loaded {len(user_schedules)} schedules from Supabase!")
    except Exception as e:
        print(f"❌ Database Load Error: {e}")
        user_schedules = {}

# 3. Function to save a specific user's data
def save_user_data(user_id: int, schedule_data: dict):
    try:
        # 'upsert' safely updates the row if the user_id exists, or inserts a new one if it doesn't
        supabase.table("schedules").upsert({
            "user_id": user_id,
            "schedule_data": schedule_data
        }).execute()
        print(f"Saved data for user {user_id}")
    except Exception as e:
        print(f"❌ Database Save Error: {e}")

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    # This function "syncs" your slash commands to Discord's servers
    async def setup_hook(self):
        print("Syncing slash commands...")
        await self.tree.sync()

bot = MyBot()

@bot.event
async def on_ready():
    load_data()
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')

# --- SLASH COMMANDS GO HERE ---
user_schedules = {}

def parse_time_to_minutes(time_str):
    """Converts '9:30' to 570."""
    h, m = map(int, time_str.split(':'))
    return h * 60 + m

def minutes_to_time(minutes):
    """Converts 570 back to '9:30 AM'."""
    hours = minutes // 60
    mins = minutes % 60

    # Figure out if it's AM or PM
    ampm = "PM" if hours >= 12 else "AM"

    # Convert to 12-hour format
    display_hour = hours % 12

    # Handle midnight and noon (so it doesn't say 0:30 AM)
    if display_hour == 0:
        display_hour = 12

    return f"{display_hour}:{mins:02d} {ampm}"

def parse_smart_times(input_string):
    """
    Takes a messy string like "9am-5pm, 18:00 to 20:00" and returns
    a list of (start_minutes, end_minutes) tuples.
    """
    # This regex captures: Hour, optional minute, optional am/pm, separator (- or to), and the same for the end time.
    pattern = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?'
    matches = re.findall(pattern, input_string.lower())

    parsed_ranges = []

    for match in matches:
        start_h, start_m, start_ampm, end_h, end_m, end_ampm = match

        # Internal helper to convert extracted groups to total minutes
        def to_minutes(h_str, m_str, ampm):
            h = int(h_str)
            m = int(m_str) if m_str else 0

            if ampm == 'pm' and h < 12:
                h += 12
            elif ampm == 'am' and h == 12:
                h = 0
            return h * 60 + m

        start_mins = to_minutes(start_h, start_m, start_ampm)
        end_mins = to_minutes(end_h, end_m, end_ampm)

        # --- SMART GUESSING LOGIC ---

        # Scenario 1: User types "9-5". It defaults to 9am to 5am.
        # Since 5am is before 9am, we can safely assume they meant 5pm.
        if end_mins <= start_mins and not end_ampm and int(end_h) < 12:
            end_mins += 12 * 60

            # Scenario 2: User types "1-5pm". It defaults to 1am to 5pm.
        # If the start hour is smaller than the end hour and they used PM at the end, they usually mean PM for both.
        if end_ampm == 'pm' and not start_ampm and int(start_h) <= int(end_h) and int(start_h) < 12:
            start_mins += 12 * 60

        parsed_ranges.append((start_mins, end_mins))

    return parsed_ranges

def get_fresh_user_data(user_id: int):
    # Fetch a single user's schedule directly from Supabase.
    try:
        response = supabase.table("schedules").select("schedule_data").eq("user_id", user_id).execute()
        if response.data:
            # Update our local cache so we have it for next time
            user_schedules[user_id] = response.data[0]['schedule_data']
            return response.data[0]['schedule_data']
        return None
    except Exception as e:
        print(f"❌ Database Fetch Error: {e}")
        return None

class WebLinkView(ui.View):
    def __init__(self):
        super().__init__()
        # Create a button that opens a web page
        self.add_item(ui.Button(
            label="Open Schedule Editor",
            url="https://availability-checker.netlify.app", # Your WebStorm link!
            style=discord.ButtonStyle.link
        ))

# --- COMMANDS ---
@bot.tree.command(name="set_availability", description="Set your availability")
async def set_detailed(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Click the button below to log in and update your availability!",
        view=WebLinkView(),
        ephemeral=True
    )
class SyncPicker(ui.View):
    def __init__(self, bot_schedules):
        super().__init__()
        self.bot_schedules = bot_schedules # Pass the data dictionary into the view

    # This creates the specialized "User Dropdown"
    @ui.select(
        cls=ui.UserSelect,
        placeholder="Select the people you want to sync with...",
        min_values=2,
        max_values=10 # You can adjust this limit
    )
    async def select_users(self, interaction: discord.Interaction, select: ui.UserSelect):
        await interaction.response.defer(ephemeral=True)
        selected_users = select.values # This is a list of Member objects
        schedules_to_compare = []
        missing_users = []

        participant_names = ", ".join([user.display_name for user in selected_users])
        participant_mentions = " ".join([user.mention for user in selected_users])

        for user in selected_users:
            data = get_fresh_user_data(user.id)
            if data:
                schedules_to_compare.append(data)
            else:
                missing_users.append(user.display_name)

        # Check if any missing users
        if missing_users:
            names = ", ".join(missing_users)
            await interaction.followup.send(f"Cannot compare schedules."
                                                    f" The following users have not set their times yet: {names}")
            return # End method call


        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        final_result = {}

        for day in days:
            # Start with first person's times for this day
            common_time = schedules_to_compare[0].get(day, [])

            # Compare against rest of list
            for next_person in schedules_to_compare[1:]:
                next_person_day = next_person.get(day, [])
                common_time = get_overlap(common_time, next_person_day)

            # Save any overlaps
            if common_time:
                final_result[day] = common_time

        if not final_result:
            await interaction.followup.send("No times in common for this group", ephemeral=True)
            return

        embed = discord.Embed(
            title="Group Availability",
            description=f"Comparing schedules for:\n{participant_names}",
            color=discord.Color.brand_green()
        )

        for day, ranges in final_result.items():
            time_strings = [f"{minutes_to_time(s)} - {minutes_to_time(e)}" for s, e in ranges]
            embed.add_field(name=day.capitalize(), value="\n".join(time_strings), inline=False)

        embed.set_footer(text=f"Generated by {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

def get_overlap(ranges_a, ranges_b):
    overlaps = []
    for a_start, a_end in ranges_a:
        for b_start, b_end in ranges_b:
            latest_start = max(a_start, b_start)
            earliest_end = min(a_end, b_end)
            if latest_start < earliest_end:
                overlaps.append((latest_start, earliest_end))
    return overlaps

@bot.tree.command(name="compare_schedules", description="Find when everyone is free")
async def compare(interaction: discord.Interaction):
    await interaction.response.send_message("Who are we checking?", view=SyncPicker(user_schedules), ephemeral=True)

@bot.tree.command(name="view_availability", description="Check a user's availability")
@app_commands.describe(member="The user you want to see")
async def view_availability(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)

    data = get_fresh_user_data(member.id)

    if not data:
        await interaction.followup.send(f"{member.display_name} has not set a schedule yet.", ephemeral=True)
        return

    days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    embed = discord.Embed(title=f"📅 {member.display_name}'s Schedule", color=discord.Color.blue())

    for day in days_of_week:
        if day.lower() in data:
            ranges = data[day.lower()]
            embed.add_field(name=day, value=range_to_string(ranges), inline=False)
        else:
            embed.add_field(name=day, value="*No times set*", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

def range_to_string(time_ranges):
    output = ""
    for start, end in time_ranges:
        output += f"{minutes_to_time(start)}-{minutes_to_time(end)}, "
    return output.rstrip(", ")


if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)