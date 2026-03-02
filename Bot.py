import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select, Modal, TextInput
from discord import app_commands
import asyncio
import os
import json
from datetime import datetime
from typing import Optional
import uuid

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_DIR = os.getenv("DATA_DIR", "/data")
DATA_FILE = os.path.join(DATA_DIR, "nexus_data.json")
os.makedirs(DATA_DIR, exist_ok=True)

CLR_MAIN = 0x0F2B46
CLR_ACCENT = 0x00D4FF
CLR_SUCCESS = 0x00E676
CLR_DANGER = 0xFF3D71
CLR_WARN = 0xFFAA00
CLR_DIM = 0x2C3E50
CLR_RECRUIT = 0x7C3AED

LANES = ["Gold Lane", "Mid Lane", "Exp Lane", "Jungler", "Roamer"]
LANE_ICONS = {"Gold Lane": "🔹", "Mid Lane": "◆", "Exp Lane": "◇", "Jungler": "✦", "Roamer": "❖"}

# Ranks ordered from lowest to highest for filtering
RANKS = ["Warrior", "Elite", "Master", "Grandmaster", "Epic", "Legend", "Mythic", "Mythical Honor", "Mythical Glory", "Mythical Immortal"]
# Base ranks (no stars needed)
BASE_RANKS = ["Warrior", "Elite", "Master", "Grandmaster", "Epic", "Legend", "Mythic", "Mythical Honor"]
# Star ranks (need star count)
STAR_RANKS = ["Mythical Glory", "Mythical Immortal"]

def rank_index(rank_str):
    """Get numeric rank value for comparison. Higher = better.
    Parses 'Mythical Glory 50 stars' -> base index + star bonus."""
    if not rank_str: return 0
    for i, r in enumerate(RANKS):
        if rank_str.startswith(r):
            base = (i + 1) * 100  # 100-1000
            # Extract stars if present
            rest = rank_str[len(r):].strip()
            stars = 0
            for part in rest.split():
                try: stars = int(part); break
                except: pass
            return base + stars
    return 0

DEFAULT_SETTINGS = {
    "verified_role": "Verified", "unverified_role": "Unverified",
    "verification_channel": "verify", "log_channel": "bot-logs",
    "find_player_channel": "find-player", "find_team_channel": "find-team"
}

# ━━━ DATA ━━━
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            data.setdefault("squads", {}); data.setdefault("players", {})
            data.setdefault("settings", DEFAULT_SETTINGS.copy())
            data.setdefault("recruitment_posts", []); data.setdefault("tryout_invites", [])
            for k, v in DEFAULT_SETTINGS.items(): data["settings"].setdefault(k, v)
            return data
    data = {"squads": {}, "players": {}, "settings": DEFAULT_SETTINGS.copy(), "recruitment_posts": [], "tryout_invites": []}
    save_data(data); return data

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, indent=2, fp=f, ensure_ascii=False)

bot_data = load_data()
def S(key): return bot_data["settings"].get(key, DEFAULT_SETTINGS.get(key, ""))

MOD_ROLE = "MODERATOR"
LEADER_ROLE = "LEADER"

async def log_action(guild, title, desc):
    if not guild: return
    ch = discord.utils.get(guild.text_channels, name=S("log_channel"))
    if not ch: return
    e = discord.Embed(title=title, description=desc, color=CLR_DIM, timestamp=datetime.utcnow())
    e.set_footer(text="🔹 Nexus Logs")
    try: await ch.send(embed=e)
    except: pass

def is_mod(m): return any(r.name == MOD_ROLE for r in m.roles)
def is_leader(m): return any(r.name == LEADER_ROLE for r in m.roles)

def get_member_squad(member, guild):
    for sq, info in bot_data["squads"].items():
        role = discord.utils.get(guild.roles, name=sq)
        if role and role in member.roles: return role, info.get("tag", "")
    return None, None

def remove_all_tags(name):
    for info in bot_data["squads"].values():
        tag = info.get("tag", "")
        if tag and name.startswith(f"{tag} "): return name[len(f"{tag} "):]
    return name

async def safe_nick(member, role, tag):
    clean = remove_all_tags(member.display_name)
    want = f"{tag} {clean}" if role and tag else clean
    if member.display_name == want: return
    try: await member.edit(nick=want); await asyncio.sleep(0.4)
    except: pass

def update_player_squad(pid, new_sq=None, old_sq=None):
    pk = str(pid)
    if pk not in bot_data["players"]:
        bot_data["players"][pk] = {"discord_id": pid, "ingame_name": "", "ingame_id": "", "highest_rank": "", "lane": "", "age_group": "", "gender": "", "squad": new_sq, "squad_history": [], "verified": False}
    p = bot_data["players"][pk]
    if old_sq and old_sq != new_sq:
        p.setdefault("squad_history", []).append({"squad": old_sq, "left_date": datetime.utcnow().isoformat()})
    p["squad"] = new_sq; save_data(bot_data)

def get_leaders_for_squad(guild, squad_role):
    lr = discord.utils.get(guild.roles, name=LEADER_ROLE)
    if not lr: return []
    return [m for m in lr.members if squad_role in m.roles]

def get_leader_names(guild, squad_role):
    return [m.display_name for m in get_leaders_for_squad(guild, squad_role)]

def format_rank_display(rank_str):
    """Format rank for display with star info."""
    if not rank_str: return "—"
    return rank_str

def get_base_rank(rank_str):
    """Extract base rank name from full rank string."""
    if not rank_str: return ""
    for r in reversed(RANKS):
        if rank_str.startswith(r): return r
    return rank_str

# ━━━ ROLE SETUP ━━━
async def ensure_roles(guild):
    existing = {r.name for r in guild.roles}
    for name in [S("unverified_role")]:
        if name not in existing: await guild.create_role(name=name, color=discord.Color.dark_grey(), reason="Nexus 🔹")
    for name in [S("verified_role")]:
        if name not in existing: await guild.create_role(name=name, color=discord.Color.from_str("#00D4FF"), reason="Nexus 🔹")
    for lane in LANES:
        if lane not in existing: await guild.create_role(name=lane, reason=f"Nexus 🔹 {lane}")
    for rank in RANKS:
        if rank not in existing: await guild.create_role(name=rank, reason=f"Nexus 🔹 {rank}")
    for name in ["18+", "Under 18", "Male", "Female"]:
        if name not in existing: await guild.create_role(name=name, reason=f"Nexus 🔹 {name}")

async def send_verify_embed(guild):
    ch = discord.utils.get(guild.text_channels, name=S("verification_channel"))
    if not ch: return
    async for msg in ch.history(limit=20):
        if msg.author == bot.user and msg.embeds:
            for em in msg.embeds:
                if em.title and "Verification" in em.title: return
    e = discord.Embed(title="🔹 Nexus ─ Verification", color=CLR_ACCENT,
        description="```\n  ╔══════════════════════════════╗\n  ║   IDENTITY  VERIFICATION     ║\n  ╚══════════════════════════════╝\n```\nComplete your profile to unlock the server.")
    e.add_field(name="▸ Process", value="**1 ·** Click `Verify` below\n**2 ·** Enter **IGN** and **Game ID**\n**3 ·** Select **Lane · Rank · Age · Gender**\n**4 ·** Click `Confirm`", inline=False)
    e.add_field(name="▸ Roles Granted", value=f"` ✓ ` **{S('verified_role')}** — full access\n` ✓ ` Lane · Rank · Age · Gender roles", inline=False)
    e.set_footer(text="🔹 Update anytime with /panel")
    await ch.send(embed=e, view=VerifyButtonView())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  R A N K   S E L E C T I O N   (shared component)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RankSelectView(View):
    """Rank selection: dropdown for all 10 ranks.
    If Mythical Glory or Mythical Immortal selected, prompts for star count."""
    def __init__(self, callback_fn, timeout=180):
        super().__init__(timeout=timeout)
        self.callback_fn = callback_fn
        sel = Select(placeholder="▸ Select your rank", options=[
            discord.SelectOption(label=r, value=r) for r in RANKS
        ])
        self._wire(sel); self.add_item(sel)

    def _wire(self, sel):
        async def cb(interaction):
            rank = interaction.data["values"][0]
            if rank in STAR_RANKS:
                await interaction.response.send_modal(StarInputModal(rank, self.callback_fn))
            else:
                await self.callback_fn(interaction, rank)
        sel.callback = cb


class StarInputModal(Modal, title="🔹 Star Count"):
    stars = TextInput(label="How many stars?", placeholder="e.g. 50, 130, 600", required=True, max_length=10)

    def __init__(self, rank, callback_fn):
        super().__init__()
        self.rank = rank
        self.callback_fn = callback_fn

    async def on_submit(self, interaction):
        try:
            star_num = int(self.stars.value.strip())
            full_rank = f"{self.rank} {star_num} stars"
        except ValueError:
            full_rank = self.rank
        await self.callback_fn(interaction, full_rank)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  V E R I F I C A T I O N
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VerifyButtonView(View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify", emoji="🔹", style=discord.ButtonStyle.success, custom_id="nexus_verify")
    async def click(self, interaction, button):
        vr = discord.utils.get(interaction.guild.roles, name=S("verified_role"))
        if vr and vr in interaction.user.roles:
            await interaction.response.send_message("Already verified. `/panel` to update.", ephemeral=True); return
        await interaction.response.send_modal(VerifyModal(interaction.user.id))


class VerifyModal(Modal, title="🔹 Nexus ─ Verification"):
    ign = TextInput(label="In-Game Name (IGN)", placeholder="Your ML IGN", required=True, max_length=50)
    gid = TextInput(label="In-Game ID", placeholder="Numeric ML ID", required=True, max_length=50)
    def __init__(self, uid): super().__init__(); self.uid = uid
    async def on_submit(self, interaction):
        v = VerifyStep2(self.uid, self.ign.value, self.gid.value)
        e = discord.Embed(title="🔹 Step 2", color=CLR_ACCENT,
            description=f"```\nIGN : {self.ign.value}\nID  : {self.gid.value}\n```\nSelect all options then **Confirm**.")
        await interaction.response.send_message(embed=e, view=v, ephemeral=True)


class VerifyStep2(View):
    def __init__(self, uid, ign, gid):
        super().__init__(timeout=300)
        self.uid, self.ign, self.gid = uid, ign, gid
        self.lane = self.rank = self.age = self.gender = None

        # Lane select
        s1 = Select(placeholder="▸ Select lane", options=[
            discord.SelectOption(label=l, value=l, description=LANE_ICONS.get(l, "")) for l in LANES
        ], row=0)
        self._wire(s1, "lane"); self.add_item(s1)

        # Rank select (all 10)
        s2 = Select(placeholder="▸ Select rank", options=[
            discord.SelectOption(label=r, value=r) for r in RANKS
        ], row=1)
        self._wire_rank(s2); self.add_item(s2)

        # Age select
        s3 = Select(placeholder="▸ Age group", options=[
            discord.SelectOption(label="18+", value="18+"),
            discord.SelectOption(label="Under 18", value="Under 18")
        ], row=2)
        self._wire(s3, "age"); self.add_item(s3)

        # Gender select
        s4 = Select(placeholder="▸ Gender", options=[
            discord.SelectOption(label="Male", value="Male"),
            discord.SelectOption(label="Female", value="Female")
        ], row=3)
        self._wire(s4, "gender"); self.add_item(s4)

        b = Button(label="Confirm", style=discord.ButtonStyle.success, row=4)
        b.callback = self._confirm; self.add_item(b)

    def _wire(self, sel, attr):
        async def cb(i): setattr(self, attr, i.data["values"][0]); await i.response.defer()
        sel.callback = cb

    def _wire_rank(self, sel):
        async def cb(interaction):
            rank = interaction.data["values"][0]
            if rank in STAR_RANKS:
                await interaction.response.send_modal(VerifyStarModal(self, rank))
            else:
                self.rank = rank
                await interaction.response.defer()
        sel.callback = cb

    async def _confirm(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Not yours.", ephemeral=True); return
        if not all([self.lane, self.rank, self.age, self.gender]):
            await interaction.response.send_message("▸ Select all four options first.", ephemeral=True); return

        guild, member = interaction.guild, interaction.user
        # Determine the Discord role name (base rank, without stars)
        base_rank = get_base_rank(self.rank)

        bot_data["players"][str(member.id)] = {
            "discord_id": member.id, "ingame_name": self.ign, "ingame_id": self.gid,
            "highest_rank": self.rank, "lane": self.lane,
            "age_group": self.age, "gender": self.gender,
            "squad": None, "squad_history": [], "verified": True
        }
        save_data(bot_data)

        add_roles = []
        for rn in [S("verified_role"), self.lane, base_rank, self.age, self.gender]:
            r = discord.utils.get(guild.roles, name=rn)
            if r: add_roles.append(r)
        try: await member.add_roles(*add_roles, reason="Nexus 🔹 Verified")
        except Exception as ex: print(f"[ERR] {ex}")

        ur = discord.utils.get(guild.roles, name=S("unverified_role"))
        if ur and ur in member.roles:
            try: await member.remove_roles(ur)
            except: pass

        li = LANE_ICONS.get(self.lane, "·")
        e = discord.Embed(title="🔹 Verified", color=CLR_SUCCESS,
            description=f"Welcome, **{member.display_name}**.")
        e.add_field(name="IGN", value=f"`{self.ign}`", inline=True)
        e.add_field(name="ID", value=f"`{self.gid}`", inline=True)
        e.add_field(name="Lane", value=f"{li} {self.lane}", inline=True)
        e.add_field(name="Rank", value=self.rank, inline=True)
        e.add_field(name="Age", value=self.age, inline=True)
        e.add_field(name="Gender", value=self.gender, inline=True)
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text="🔹 /panel to manage")
        await interaction.response.edit_message(embed=e, view=None)
        await log_action(guild, "🔹 Verified", f"{member.mention} ─ **{self.ign}** ─ {self.lane} ─ {self.rank}")


class VerifyStarModal(Modal, title="🔹 Star Count"):
    """Star input during verification."""
    stars = TextInput(label="How many stars?", placeholder="e.g. 50, 130, 600", required=True, max_length=10)

    def __init__(self, parent_view, rank):
        super().__init__()
        self.parent_view = parent_view
        self.rank = rank

    async def on_submit(self, interaction):
        try:
            star_num = int(self.stars.value.strip())
            self.parent_view.rank = f"{self.rank} {star_num} stars"
        except ValueError:
            self.parent_view.rank = self.rank
        await interaction.response.send_message(
            f"▸ Rank set to **{self.parent_view.rank}**. Now click **Confirm**.", ephemeral=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  R E C R U I T M E N T:  Leader posts looking for player
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RecruitPostModal(Modal, title="🔹 Recruitment Post"):
    description = TextInput(label="What are you looking for?", placeholder="Playstyle, schedule, requirements...", required=True, style=discord.TextStyle.long, max_length=500)
    min_rank = TextInput(label="Minimum Rank", placeholder="e.g. Epic, Legend, Mythic", required=False, max_length=30)

    def __init__(self, sq_name, sq_tag, lane, leader_id):
        super().__init__()
        self.sq_name, self.sq_tag, self.lane, self.leader_id = sq_name, sq_tag, lane, leader_id

    async def on_submit(self, interaction):
        ch = discord.utils.get(interaction.guild.text_channels, name=S("find_player_channel"))
        if not ch:
            await interaction.response.send_message(f"▸ `#{S('find_player_channel')}` not found.", ephemeral=True); return

        pid = str(uuid.uuid4())[:8]
        li = LANE_ICONS.get(self.lane, "·")
        e = discord.Embed(title=f"🔹 {self.sq_tag} {self.sq_name} ─ Recruiting", color=CLR_RECRUIT,
            description=f"```\n  Lane needed : {li} {self.lane}\n  Min Rank   : {self.min_rank.value or 'Any'}\n  Posted by  : {interaction.user.display_name}\n```")
        e.add_field(name="▸ Details", value=self.description.value, inline=False)
        e.add_field(name="▸ Squad", value=f"`{self.sq_tag}` **{self.sq_name}**", inline=True)
        e.add_field(name="▸ Lane", value=f"{li} {self.lane}", inline=True)
        if self.min_rank.value:
            e.add_field(name="▸ Min Rank", value=self.min_rank.value, inline=True)
        e.set_footer(text=f"ID: {pid} ─ Click Apply to send your profile")
        e.timestamp = datetime.utcnow()
        if interaction.user.display_avatar:
            e.set_thumbnail(url=interaction.user.display_avatar.url)

        post = {"post_id": pid, "type": "find_player", "squad_name": self.sq_name,
                "squad_tag": self.sq_tag, "lane": self.lane, "leader_id": self.leader_id,
                "guild_id": interaction.guild.id, "date": datetime.utcnow().isoformat()}
        bot_data["recruitment_posts"].append(post); save_data(bot_data)

        view = ApplyToSquadBtn(pid, self.sq_name, self.sq_tag, self.leader_id)
        await ch.send(embed=e, view=view)
        await interaction.response.send_message(f"▸ Posted in `#{S('find_player_channel')}`. ID: `{pid}`", ephemeral=True)
        await log_action(interaction.guild, "🔹 Recruitment", f"{interaction.user.mention} ─ {li} {self.lane} for **{self.sq_name}**")


class ApplyToSquadBtn(View):
    """Persistent Apply button on recruitment posts in #find-player."""
    def __init__(self, pid, sq, tag, lid):
        super().__init__(timeout=None)
        self.pid, self.sq, self.tag, self.lid = pid, sq, tag, lid
        btn = Button(label="Apply", emoji="▸", style=discord.ButtonStyle.success, custom_id=f"apply_{pid}")
        btn.callback = self._apply; self.add_item(btn)

    async def _apply(self, interaction):
        p = bot_data["players"].get(str(interaction.user.id))
        if not p or not p.get("ingame_name"):
            await interaction.response.send_message("▸ Profile needed. `/panel` ▸ Edit Profile.", ephemeral=True); return
        if p.get("squad") == self.sq:
            await interaction.response.send_message("▸ Already in this squad.", ephemeral=True); return

        guild = interaction.guild
        leader = guild.get_member(self.lid)
        if not leader:
            sr = discord.utils.get(guild.roles, name=self.sq)
            leaders = get_leaders_for_squad(guild, sr) if sr else []
            leader = leaders[0] if leaders else None

        if leader:
            li = LANE_ICONS.get(p.get("lane", ""), "·")
            try:
                de = discord.Embed(title=f"🔹 Application ─ {self.tag} {self.sq}", color=CLR_RECRUIT,
                    description=f"**{interaction.user.display_name}** wants to join.")
                de.add_field(name="IGN", value=f"`{p.get('ingame_name')}`", inline=True)
                de.add_field(name="ID", value=f"`#{p.get('ingame_id')}`", inline=True)
                de.add_field(name="Lane", value=f"{li} {p.get('lane')}", inline=True)
                de.add_field(name="Rank", value=p.get("highest_rank", "—"), inline=True)
                extras = [x for x in [p.get("age_group"), p.get("gender")] if x]
                if extras: de.add_field(name="Info", value=" · ".join(extras), inline=True)
                de.set_thumbnail(url=interaction.user.display_avatar.url)
                de.set_footer(text="/leader_panel to recruit")
                await leader.send(embed=de)
            except: pass

        await interaction.response.send_message(f"▸ Application sent to **{self.tag} {self.sq}** leadership.", ephemeral=True)
        await log_action(guild, "🔹 Applied", f"{interaction.user.mention} → **{self.sq}**")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  R E C R U I T M E N T:  Leader SEARCH + TRYOUT system
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SearchFiltersView(View):
    """Leader selects lane (required) + optional filters before searching."""
    def __init__(self, sq, tag, leader_id):
        super().__init__(timeout=300)
        self.sq, self.tag, self.leader_id = sq, tag, leader_id
        self.lane = None
        self.min_rank_filter = None
        self.gender_filter = None

        # Row 0: Lane (required)
        s1 = Select(placeholder="▸ Lane needed (required)", options=[
            discord.SelectOption(label=l, value=l, description=LANE_ICONS.get(l, "")) for l in LANES
        ], row=0)
        async def lane_cb(i):
            self.lane = i.data["values"][0]; await i.response.defer()
        s1.callback = lane_cb; self.add_item(s1)

        # Row 1: Min rank filter (optional)
        s2 = Select(placeholder="▸ Min rank filter (optional)", options=[
            discord.SelectOption(label="Any Rank", value="any"),
        ] + [discord.SelectOption(label=r, value=r) for r in RANKS], row=1)
        async def rank_cb(i):
            val = i.data["values"][0]
            self.min_rank_filter = None if val == "any" else val
            await i.response.defer()
        s2.callback = rank_cb; self.add_item(s2)

        # Row 2: Gender filter (optional)
        s3 = Select(placeholder="▸ Gender filter (optional)", options=[
            discord.SelectOption(label="Any Gender", value="any"),
            discord.SelectOption(label="Male", value="Male"),
            discord.SelectOption(label="Female", value="Female"),
        ], row=2)
        async def gender_cb(i):
            val = i.data["values"][0]
            self.gender_filter = None if val == "any" else val
            await i.response.defer()
        s3.callback = gender_cb; self.add_item(s3)

        # Row 3: Search button
        btn = Button(label="Search", emoji="⊙", style=discord.ButtonStyle.success, row=3)
        btn.callback = self._search; self.add_item(btn)

    async def _search(self, interaction):
        if interaction.user.id != self.leader_id:
            await interaction.response.send_message("Not yours.", ephemeral=True); return
        if not self.lane:
            await interaction.response.send_message("▸ Select a lane first.", ephemeral=True); return

        li = LANE_ICONS.get(self.lane, "·")
        min_rank_val = rank_index(self.min_rank_filter) if self.min_rank_filter else 0

        matches = []
        for pk, p in bot_data["players"].items():
            if not (p.get("lane") == self.lane and p.get("verified") and p.get("ingame_name")):
                continue
            if p.get("squad") == self.sq:
                continue  # Skip own squad
            # Apply rank filter
            if min_rank_val > 0 and rank_index(p.get("highest_rank", "")) < min_rank_val:
                continue
            # Apply gender filter
            if self.gender_filter and p.get("gender") != self.gender_filter:
                continue
            m = interaction.guild.get_member(p.get("discord_id"))
            if m: matches.append((m, p))

        # Build filter description
        filters = [f"{li} **{self.lane}**"]
        if self.min_rank_filter: filters.append(f"Min: **{self.min_rank_filter}**+")
        if self.gender_filter: filters.append(f"Gender: **{self.gender_filter}**")
        filter_text = " · ".join(filters)

        if not matches:
            e = discord.Embed(title="🔹 No Results", color=CLR_WARN,
                description=f"No players found matching:\n{filter_text}")
            await interaction.response.edit_message(embed=e, view=None); return

        matches = matches[:15]
        e = discord.Embed(title=f"🔹 Search Results", color=CLR_ACCENT,
            description=f"Filters: {filter_text}\n**{len(matches)}** player(s) found. Select one to send a **tryout invitation**.")

        for m, p in matches:
            sq = p.get("squad")
            sq_text = f"`{bot_data['squads'].get(sq, {}).get('tag', '')}` {sq}" if sq and sq in bot_data["squads"] else "Free Agent"
            rank_display = p.get("highest_rank", "—")
            gender_display = f" · {p.get('gender')}" if p.get("gender") else ""
            e.add_field(
                name=f"{li} {m.display_name}",
                value=f"IGN: `{p.get('ingame_name')}` ─ {rank_display}{gender_display} ─ {sq_text}",
                inline=False
            )

        view = TryoutSelectView(matches, self.sq, self.tag, self.leader_id, interaction.guild.id)
        await interaction.response.edit_message(embed=e, view=view)


class TryoutSelectView(View):
    """Leader selects a player from search results to send tryout invite."""
    def __init__(self, matches, sq, tag, leader_id, guild_id):
        super().__init__(timeout=180)
        self.sq, self.tag, self.leader_id, self.guild_id = sq, tag, leader_id, guild_id
        self.mp = {str(m.id): (m, p) for m, p in matches}

        sel = Select(placeholder="▸ Select player for tryout invite", options=[
            discord.SelectOption(
                label=f"{m.display_name} ─ {p.get('ingame_name', '?')}",
                value=str(m.id),
                description=f"{p.get('highest_rank', '?')} ─ {p.get('squad') or 'Free Agent'}"[:100]
            ) for m, p in matches
        ])
        self._wire(sel); self.add_item(sel)

    def _wire(self, sel):
        async def cb(interaction):
            if interaction.user.id != self.leader_id:
                await interaction.response.send_message("Not yours.", ephemeral=True); return

            mid = interaction.data["values"][0]
            member, p = self.mp[mid]
            leader = interaction.user

            invite_id = str(uuid.uuid4())[:8]

            # Save tryout invite
            bot_data["tryout_invites"].append({
                "invite_id": invite_id,
                "player_id": member.id,
                "leader_id": leader.id,
                "squad_name": self.sq,
                "squad_tag": self.tag,
                "guild_id": self.guild_id,
                "status": "pending",
                "date": datetime.utcnow().isoformat()
            })
            save_data(bot_data)

            # DM the player with tryout invitation (Accept/Decline buttons)
            try:
                li = LANE_ICONS.get(p.get("lane", ""), "·")
                dm_embed = discord.Embed(
                    title="🔹 Tryout Invitation",
                    description=(
                        f"`{self.tag}` **{self.sq}** is interested in you!\n\n"
                        f"**{leader.display_name}** (`{leader.name}`) would like to invite you "
                        f"for a tryout with their squad."
                    ),
                    color=CLR_RECRUIT
                )
                dm_embed.add_field(name="Squad", value=f"`{self.tag}` **{self.sq}**", inline=True)
                dm_embed.add_field(name="Leader", value=f"**{leader.display_name}**\n`@{leader.name}`", inline=True)
                dm_embed.add_field(name="What happens if you accept?", value=(
                    "` ✓ ` You'll get the squad's **guest role**\n"
                    "` ✓ ` Access to their **voice channels**\n"
                    "` ✓ ` You can try out with the team\n"
                    "` ✓ ` No commitment — just a tryout"
                ), inline=False)
                if leader.display_avatar:
                    dm_embed.set_thumbnail(url=leader.display_avatar.url)
                dm_embed.set_footer(text=f"Invite ID: {invite_id}")

                view = TryoutResponseView(invite_id)
                await member.send(embed=dm_embed, view=view)
                dm_sent = True
            except discord.Forbidden:
                dm_sent = False

            if dm_sent:
                e = discord.Embed(title="🔹 Tryout Sent", color=CLR_SUCCESS,
                    description=(
                        f"Tryout invitation sent to **{member.display_name}**.\n\n"
                        f"If they **accept**, they'll automatically receive the guest role "
                        f"for **{self.sq}** and gain access to your voice channels.\n\n"
                        f"Invite ID: `{invite_id}`"
                    ))
            else:
                e = discord.Embed(title="🔹 DM Failed", color=CLR_WARN,
                    description=f"**{member.display_name}** has DMs disabled.")

            await interaction.response.edit_message(embed=e, view=None)
            await log_action(interaction.guild, "🔹 Tryout Sent",
                f"{leader.mention} invited {member.mention} for tryout at **{self.sq}**")
        sel.callback = cb


class TryoutResponseView(View):
    """Persistent Accept/Decline buttons sent in DM to the player."""
    def __init__(self, invite_id):
        super().__init__(timeout=None)
        self.invite_id = invite_id

        accept = Button(label="Accept Tryout", emoji="✓", style=discord.ButtonStyle.success,
                        custom_id=f"tryout_accept_{invite_id}")
        accept.callback = self._accept; self.add_item(accept)

        decline = Button(label="Decline", emoji="×", style=discord.ButtonStyle.secondary,
                         custom_id=f"tryout_decline_{invite_id}")
        decline.callback = self._decline; self.add_item(decline)

    def _find_invite(self):
        for inv in bot_data.get("tryout_invites", []):
            if inv["invite_id"] == self.invite_id: return inv
        return None

    async def _accept(self, interaction):
        inv = self._find_invite()
        if not inv:
            await interaction.response.edit_message(content="▸ Invite expired.", embed=None, view=None); return
        if inv["status"] != "pending":
            await interaction.response.edit_message(
                content=f"▸ Already {inv['status']}.", embed=None, view=None); return

        inv["status"] = "accepted"; save_data(bot_data)

        # Give guest role
        guild = bot.get_guild(inv["guild_id"])
        if guild:
            member = guild.get_member(interaction.user.id)
            sq_info = bot_data["squads"].get(inv["squad_name"], {})
            guest_role_name = sq_info.get("guest_role")

            if member and guest_role_name:
                gr = discord.utils.get(guild.roles, name=guest_role_name)
                if gr:
                    try: await member.add_roles(gr, reason=f"Nexus 🔹 Tryout accepted for {inv['squad_name']}")
                    except: pass

            # Notify the leader
            leader = guild.get_member(inv["leader_id"])
            if leader:
                try:
                    notify = discord.Embed(title="🔹 Tryout Accepted", color=CLR_SUCCESS,
                        description=(
                            f"**{interaction.user.display_name}** accepted the tryout for "
                            f"`{inv['squad_tag']}` **{inv['squad_name']}**!\n\n"
                            f"They now have the guest role and can access your voice channels."
                        ))
                    notify.set_thumbnail(url=interaction.user.display_avatar.url)
                    await leader.send(embed=notify)
                except: pass

            await log_action(guild, "🔹 Tryout Accepted",
                f"{interaction.user.mention} accepted tryout for **{inv['squad_name']}**")

        e = discord.Embed(title="🔹 Tryout Accepted", color=CLR_SUCCESS,
            description=(
                f"You've accepted the tryout for `{inv['squad_tag']}` **{inv['squad_name']}**!\n\n"
                f"` ✓ ` Guest role assigned\n"
                f"` ✓ ` You can now access their voice channels\n\n"
                f"Good luck with the tryout!"
            ))
        await interaction.response.edit_message(embed=e, view=None)

    async def _decline(self, interaction):
        inv = self._find_invite()
        if not inv:
            await interaction.response.edit_message(content="▸ Invite expired.", embed=None, view=None); return
        if inv["status"] != "pending":
            await interaction.response.edit_message(
                content=f"▸ Already {inv['status']}.", embed=None, view=None); return

        inv["status"] = "declined"; save_data(bot_data)

        guild = bot.get_guild(inv["guild_id"])
        if guild:
            leader = guild.get_member(inv["leader_id"])
            if leader:
                try:
                    notify = discord.Embed(title="🔹 Tryout Declined", color=CLR_WARN,
                        description=f"**{interaction.user.display_name}** declined the tryout for **{inv['squad_name']}**.")
                    await leader.send(embed=notify)
                except: pass
            await log_action(guild, "🔹 Tryout Declined",
                f"{interaction.user.mention} declined tryout for **{inv['squad_name']}**")

        e = discord.Embed(title="🔹 Declined", color=CLR_DIM,
            description=f"You declined the tryout for **{inv['squad_name']}**.\nNo worries — you can always apply later.")
        await interaction.response.edit_message(embed=e, view=None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  R E C R U I T M E N T:  Member posts LFT + Apply direct
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FindTeamModal(Modal, title="🔹 Looking for Team"):
    description = TextInput(label="What kind of team?", placeholder="Describe what you want...", required=True, style=discord.TextStyle.long, max_length=500)
    def __init__(self, uid): super().__init__(); self.uid = uid
    async def on_submit(self, interaction):
        p = bot_data["players"].get(str(self.uid))
        if not p or not p.get("ingame_name"):
            await interaction.response.send_message("▸ Profile needed.", ephemeral=True); return
        ch = discord.utils.get(interaction.guild.text_channels, name=S("find_team_channel"))
        if not ch:
            await interaction.response.send_message(f"▸ `#{S('find_team_channel')}` not found.", ephemeral=True); return

        pid = str(uuid.uuid4())[:8]
        member = interaction.user
        li = LANE_ICONS.get(p.get("lane", ""), "·")
        sq = p.get("squad")
        sq_t = f"`{bot_data['squads'].get(sq, {}).get('tag', '?')}` {sq}" if sq and sq != "Free Agent" and sq in bot_data["squads"] else "Free Agent"

        e = discord.Embed(title=f"🔹 {member.display_name} ─ Looking for Team", color=CLR_RECRUIT,
            description=f"```\n  IGN     : {p.get('ingame_name', '—')}\n  ID      : #{p.get('ingame_id', '—')}\n  Lane    : {li} {p.get('lane', '—')}\n  Rank    : {p.get('highest_rank', '—')}\n  Current : {sq_t}\n```")
        e.add_field(name="▸ Looking For", value=self.description.value, inline=False)
        extras = [x for x in [p.get("age_group"), p.get("gender")] if x]
        if extras: e.add_field(name="▸ Info", value=" · ".join(extras), inline=True)
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"ID: {pid} ─ Leaders: click Recruit")
        e.timestamp = datetime.utcnow()

        post = {"post_id": pid, "type": "find_team", "player_id": self.uid,
                "guild_id": interaction.guild.id, "date": datetime.utcnow().isoformat()}
        bot_data["recruitment_posts"].append(post); save_data(bot_data)

        view = RecruitBtn(pid, self.uid)
        await ch.send(embed=e, view=view)
        await interaction.response.send_message(f"▸ Posted in `#{S('find_team_channel')}`. ID: `{pid}`", ephemeral=True)
        await log_action(interaction.guild, "🔹 LFT", f"{member.mention} posted LFT")


class RecruitBtn(View):
    """Persistent Recruit button on LFT posts in #find-team."""
    def __init__(self, pid, player_id):
        super().__init__(timeout=None)
        self.pid, self.player_id = pid, player_id
        btn = Button(label="Recruit", emoji="▸", style=discord.ButtonStyle.primary,
                     custom_id=f"recruit_{pid}")
        btn.callback = self._recruit; self.add_item(btn)

    async def _recruit(self, interaction):
        if not is_leader(interaction.user):
            await interaction.response.send_message("▸ Leaders only.", ephemeral=True); return
        role, tag = get_member_squad(interaction.user, interaction.guild)
        if not role:
            await interaction.response.send_message("▸ Must be in a squad.", ephemeral=True); return

        player = interaction.guild.get_member(self.player_id)
        if not player:
            await interaction.response.send_message("▸ Player left server.", ephemeral=True); return
        p = bot_data["players"].get(str(self.player_id))
        if not p:
            await interaction.response.send_message("▸ No profile.", ephemeral=True); return

        leader = interaction.user
        invite_id = str(uuid.uuid4())[:8]

        bot_data["tryout_invites"].append({
            "invite_id": invite_id, "player_id": player.id, "leader_id": leader.id,
            "squad_name": role.name, "squad_tag": tag,
            "guild_id": interaction.guild.id, "status": "pending",
            "date": datetime.utcnow().isoformat()
        })
        save_data(bot_data)

        try:
            dm_embed = discord.Embed(
                title="🔹 Tryout Invitation",
                description=(
                    f"`{tag}` **{role.name}** is interested in you!\n\n"
                    f"**{leader.display_name}** (`{leader.name}`) invites you for a tryout."
                ),
                color=CLR_RECRUIT
            )
            dm_embed.add_field(name="Squad", value=f"`{tag}` **{role.name}**", inline=True)
            dm_embed.add_field(name="Leader", value=f"**{leader.display_name}**\n`@{leader.name}`", inline=True)
            dm_embed.add_field(name="If you accept", value=(
                "` ✓ ` Guest role for the squad\n"
                "` ✓ ` Access to voice channels\n"
                "` ✓ ` No commitment — just a tryout"
            ), inline=False)
            if leader.display_avatar: dm_embed.set_thumbnail(url=leader.display_avatar.url)
            dm_embed.set_footer(text=f"Invite ID: {invite_id}")

            view = TryoutResponseView(invite_id)
            await player.send(embed=dm_embed, view=view)
            ok = True
        except discord.Forbidden:
            ok = False

        if ok:
            await interaction.response.send_message(
                f"▸ Tryout invitation sent to **{player.display_name}**.\n"
                f"If they accept, they'll get the guest role automatically.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"▸ **{player.display_name}** has DMs off.", ephemeral=True)
        await log_action(interaction.guild, "🔹 Tryout Sent",
            f"{leader.mention} → {player.mention} for **{role.name}**")


class ApplyDirectView(View):
    """Member picks a squad to apply to directly."""
    def __init__(self, uid, page=1):
        super().__init__(timeout=180)
        self.uid, self.page = uid, page
        all_sq = sorted(bot_data["squads"].items())
        start = (page - 1) * 25; page_sq = all_sq[start:start + 25]
        if not page_sq: return

        sel = Select(placeholder="▸ Select squad", options=[
            discord.SelectOption(label=n, value=n, description=f"Tag: {info.get('tag', '?')}")
            for n, info in page_sq
        ])
        self._wire(sel); self.add_item(sel)

    def _wire(self, sel):
        async def cb(interaction):
            if interaction.user.id != self.uid:
                await interaction.response.send_message("Not yours.", ephemeral=True); return
            sq_name = interaction.data["values"][0]
            member = interaction.user
            p = bot_data["players"].get(str(member.id))
            if not p or not p.get("ingame_name"):
                await interaction.response.edit_message(content="▸ Profile needed.", embed=None, view=None); return
            if p.get("squad") == sq_name:
                await interaction.response.edit_message(content="▸ Already in this squad.", embed=None, view=None); return

            guild = interaction.guild
            tag = bot_data["squads"][sq_name].get("tag", "?")
            sr = discord.utils.get(guild.roles, name=sq_name)
            leaders = get_leaders_for_squad(guild, sr) if sr else []
            if not leaders:
                await interaction.response.edit_message(content=f"▸ **{sq_name}** has no leaders.", embed=None, view=None); return

            li = LANE_ICONS.get(p.get("lane", ""), "·")
            sent = 0
            for leader in leaders:
                try:
                    de = discord.Embed(title=f"🔹 Application ─ {tag} {sq_name}", color=CLR_RECRUIT,
                        description=f"**{member.display_name}** wants to join.")
                    de.add_field(name="IGN", value=f"`{p.get('ingame_name')}`", inline=True)
                    de.add_field(name="ID", value=f"`#{p.get('ingame_id')}`", inline=True)
                    de.add_field(name="Lane", value=f"{li} {p.get('lane')}", inline=True)
                    de.add_field(name="Rank", value=p.get("highest_rank", "—"), inline=True)
                    extras = [x for x in [p.get("age_group"), p.get("gender")] if x]
                    if extras: de.add_field(name="Info", value=" · ".join(extras), inline=True)
                    de.set_thumbnail(url=member.display_avatar.url)
                    de.set_footer(text="/leader_panel to recruit")
                    await leader.send(embed=de); sent += 1
                except: pass

            if sent:
                e = discord.Embed(title="🔹 Sent", color=CLR_SUCCESS,
                    description=f"Application to `{tag}` **{sq_name}** sent to {sent} leader(s).")
            else:
                e = discord.Embed(title="🔹 Failed", color=CLR_WARN, description="Leaders have DMs off.")
            await interaction.response.edit_message(embed=e, view=None)
            await log_action(guild, "🔹 Direct Apply", f"{member.mention} → **{sq_name}**")
        sel.callback = cb


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  C O R E   M O D A L S
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ProfileEditModal(Modal, title="🔹 Edit Profile"):
    ign = TextInput(label="In-Game Name", required=False, max_length=50)
    gid = TextInput(label="In-Game ID", required=False, max_length=50)

    def __init__(self, uid, sq, lane, rank_val, existing=None):
        super().__init__()
        self.uid, self.sq, self.lane, self.rank_val = uid, sq, lane, rank_val
        if existing:
            if existing.get("ingame_name"): self.ign.default = existing["ingame_name"]
            if existing.get("ingame_id"): self.gid.default = existing["ingame_id"]

    async def on_submit(self, interaction):
        pk = str(self.uid)
        p = bot_data["players"].get(pk, {
            "discord_id": self.uid, "ingame_name": "", "ingame_id": "",
            "highest_rank": "", "lane": "", "age_group": "", "gender": "",
            "squad": self.sq, "squad_history": [], "verified": True
        })
        if self.ign.value: p["ingame_name"] = self.ign.value
        if self.gid.value: p["ingame_id"] = self.gid.value
        p["highest_rank"] = self.rank_val
        p["lane"] = self.lane
        p["squad"] = self.sq
        bot_data["players"][pk] = p; save_data(bot_data)

        li = LANE_ICONS.get(self.lane, "·")
        e = discord.Embed(title="🔹 Updated", color=CLR_SUCCESS)
        e.add_field(name="IGN", value=f"`{p['ingame_name'] or '—'}`", inline=True)
        e.add_field(name="ID", value=f"`{p['ingame_id'] or '—'}`", inline=True)
        e.add_field(name="Rank", value=self.rank_val or "—", inline=True)
        e.add_field(name="Lane", value=f"{li} {self.lane}", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)


class CreateSquadModal(Modal, title="🔹 Create Squad"):
    name = TextInput(label="Squad Name", required=True, max_length=50)
    tag = TextInput(label="Squad Tag", required=True, max_length=10)
    async def on_submit(self, interaction):
        n, t = self.name.value.strip(), self.tag.value.strip()
        if n in bot_data["squads"]:
            await interaction.response.send_message(f"▸ **{n}** exists.", ephemeral=True); return
        try: await interaction.guild.create_role(name=n, color=discord.Color.from_str("#00D4FF"), reason=f"Nexus 🔹 {n}")
        except Exception as ex: await interaction.response.send_message(f"▸ Failed: {ex}", ephemeral=True); return
        gn = f"{n.replace(' ', '.')}_guest"
        try: await interaction.guild.create_role(name=gn, color=discord.Color.dark_grey(), reason=f"Nexus 🔹 Guest: {n}")
        except: pass
        bot_data["squads"][n] = {"tag": t, "main_roster": [], "subs": [], "guest_role": gn,
            "created_by": interaction.user.id, "created_at": datetime.utcnow().isoformat()}
        save_data(bot_data)
        await interaction.response.send_message(embed=discord.Embed(title="🔹 Created", color=CLR_SUCCESS, description=f"`{t}` **{n}** is active."))
        await log_action(interaction.guild, "🔹 Squad Created", f"{interaction.user.mention} ─ **{t} {n}**")


class DeleteSquadModal(Modal, title="🔹 Delete Squad"):
    name = TextInput(label="Squad Name", required=True)
    confirm = TextInput(label="Type CONFIRM", required=True, max_length=10)
    async def on_submit(self, interaction):
        n = self.name.value.strip()
        if self.confirm.value.upper() != "CONFIRM":
            await interaction.response.send_message("▸ Type `CONFIRM`.", ephemeral=True); return
        if n not in bot_data["squads"]:
            await interaction.response.send_message(f"▸ **{n}** not found.", ephemeral=True); return
        info = bot_data["squads"][n]
        role = discord.utils.get(interaction.guild.roles, name=n)
        if role:
            try: await role.delete(reason="Nexus 🔹")
            except: pass
        gn = info.get("guest_role")
        if gn:
            gr = discord.utils.get(interaction.guild.roles, name=gn)
            if gr:
                try: await gr.delete(reason="Nexus 🔹")
                except: pass
        del bot_data["squads"][n]; save_data(bot_data)
        await interaction.response.send_message(embed=discord.Embed(title="🔹 Deleted", color=CLR_DANGER, description=f"**{n}** removed."))
        await log_action(interaction.guild, "🔹 Deleted", f"{interaction.user.mention} ─ **{n}**")


class ConfigModal(Modal, title="🔹 Configure Roles"):
    ver_role = TextInput(label="Verified Role", required=False, max_length=50)
    unver_role = TextInput(label="Unverified Role", required=False, max_length=50)
    ver_ch = TextInput(label="Verification Channel", required=False, max_length=50)
    log_ch = TextInput(label="Log Channel", required=False, max_length=50)
    def __init__(self):
        super().__init__()
        self.ver_role.default = S("verified_role"); self.unver_role.default = S("unverified_role")
        self.ver_ch.default = S("verification_channel"); self.log_ch.default = S("log_channel")
    async def on_submit(self, interaction):
        s = bot_data["settings"]; changed = []
        for field, key in [(self.ver_role, "verified_role"), (self.unver_role, "unverified_role"),
                           (self.ver_ch, "verification_channel"), (self.log_ch, "log_channel")]:
            if field.value and field.value != s.get(key):
                if key in ["verified_role", "unverified_role"]:
                    old = discord.utils.get(interaction.guild.roles, name=s[key])
                    if old:
                        try: await old.edit(name=field.value, reason="Nexus 🔹")
                        except: pass
                s[key] = field.value; changed.append(f"{key} → `{field.value}`")
        save_data(bot_data)
        desc = "\n".join(f"` ✓ ` {c}" for c in changed) if changed else "No changes."
        await interaction.response.send_message(embed=discord.Embed(title="🔹 Updated", color=CLR_SUCCESS, description=desc), ephemeral=True)


class ConfigChannelsModal(Modal, title="🔹 Recruitment Channels"):
    fp = TextInput(label="Find Player Channel", required=False, max_length=50)
    ft = TextInput(label="Find Team Channel", required=False, max_length=50)
    def __init__(self):
        super().__init__()
        self.fp.default = S("find_player_channel"); self.ft.default = S("find_team_channel")
    async def on_submit(self, interaction):
        s = bot_data["settings"]; changed = []
        if self.fp.value and self.fp.value != s.get("find_player_channel"):
            s["find_player_channel"] = self.fp.value; changed.append(f"Find Player → `#{self.fp.value}`")
        if self.ft.value and self.ft.value != s.get("find_team_channel"):
            s["find_team_channel"] = self.ft.value; changed.append(f"Find Team → `#{self.ft.value}`")
        save_data(bot_data)
        desc = "\n".join(f"` ✓ ` {c}" for c in changed) if changed else "No changes."
        await interaction.response.send_message(embed=discord.Embed(title="🔹 Updated", color=CLR_SUCCESS, description=desc), ephemeral=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S Q U A D / P R O F I L E   D I S P L A Y
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def show_squad_info(interaction, squad_role, squad_name, tag, public=False):
    info = bot_data["squads"].get(squad_name, {})
    e = discord.Embed(title=f"🔹 {squad_name}", description=f"```\nTag: {tag}\n```",
        color=squad_role.color if squad_role else CLR_MAIN)
    mains = info.get("main_roster", [])
    if mains:
        txt = ""
        for pid in mains[:5]:
            pd = bot_data["players"].get(str(pid), {}); m = interaction.guild.get_member(pid)
            if pd and pd.get("ingame_name"):
                li = LANE_ICONS.get(pd.get("lane", ""), "·")
                txt += f"{li} **{m.display_name if m else '?'}** ─ {pd['ingame_name']} `#{pd.get('ingame_id', '—')}` ─ {pd.get('highest_rank', '—')}\n"
            elif m: txt += f"· **{m.display_name}**\n"
        if txt: e.add_field(name=f"▸ Main ({len(mains)}/5)", value=txt, inline=False)
    subs = info.get("subs", [])
    if subs:
        txt = ""
        for pid in subs[:3]:
            pd = bot_data["players"].get(str(pid), {}); m = interaction.guild.get_member(pid)
            if pd and pd.get("ingame_name"):
                txt += f"{LANE_ICONS.get(pd.get('lane',''),'·')} **{m.display_name if m else '?'}** ─ {pd['ingame_name']}\n"
            elif m: txt += f"· **{m.display_name}**\n"
        if txt: e.add_field(name=f"▸ Subs ({len(subs)}/3)", value=txt, inline=False)
    if not mains and not subs and squad_role:
        txt = ""
        for m in squad_role.members[:20]:
            pd = bot_data["players"].get(str(m.id), {})
            if pd and pd.get("ingame_name"):
                txt += f"{LANE_ICONS.get(pd.get('lane',''),'·')} **{m.display_name}** ─ {pd['ingame_name']}\n"
            else: txt += f"· **{m.display_name}**\n"
        if txt: e.add_field(name=f"▸ Members ({len(squad_role.members)})", value=txt, inline=False)
    leaders = get_leader_names(interaction.guild, squad_role) if squad_role else []
    if leaders: e.add_field(name="▸ Leaders", value=", ".join(leaders), inline=False)
    gn = info.get("guest_role")
    if gn:
        gr = discord.utils.get(interaction.guild.roles, name=gn)
        if gr and gr.members:
            e.add_field(name="▸ Guests", value=", ".join(m.display_name for m in gr.members[:10]), inline=False)
    e.set_footer(text="🔹 Nexus")
    await interaction.response.send_message(embed=e, ephemeral=not public)


async def show_profile(interaction, member, public=False):
    pk = str(member.id); p = bot_data["players"].get(pk)
    if not p or not p.get("ingame_name"):
        e = discord.Embed(title="🔹 Not Found", color=CLR_DIM, description=f"{member.mention} has no profile.")
        e.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=e, ephemeral=not public); return
    sq = p.get("squad"); sr, st = None, ""
    if sq and sq in bot_data["squads"]:
        st = bot_data["squads"][sq].get("tag", ""); sr = discord.utils.get(interaction.guild.roles, name=sq)
    roster = "Member"
    if sq and sq in bot_data["squads"]:
        si = bot_data["squads"][sq]
        if member.id in si.get("main_roster", []): roster = "Main"
        elif member.id in si.get("subs", []): roster = "Sub"
    lane = p.get("lane", "—"); li = LANE_ICONS.get(lane, "·")
    e = discord.Embed(title=f"🔹 {p.get('ingame_name', '—')}", description=f"*{member.mention}*",
        color=sr.color if sr else CLR_MAIN)
    e.add_field(name="IGN", value=f"`{p.get('ingame_name', '—')}`", inline=True)
    e.add_field(name="ID", value=f"`#{p.get('ingame_id', '—')}`", inline=True)
    e.add_field(name="Lane", value=f"{li} {lane}", inline=True)
    e.add_field(name="Rank", value=p.get("highest_rank", "—"), inline=True)
    extras = [x for x in [p.get("age_group"), p.get("gender")] if x]
    if extras: e.add_field(name="Info", value=" · ".join(extras), inline=True)
    if sq and sq != "Free Agent":
        e.add_field(name="Squad", value=f"`{st}` **{sq}** ─ {roster}", inline=False)
    else: e.add_field(name="Squad", value="Free Agent", inline=False)
    sh = p.get("squad_history", [])
    if sh:
        txt = ""
        for entry in sh[-5:]:
            s = entry.get("squad", "?"); t = bot_data["squads"].get(s, {}).get("tag", "?")
            try: d = datetime.fromisoformat(entry.get("left_date", "")).strftime("%b %Y")
            except: d = "?"
            txt += f"`{t}` {s} ─ left {d}\n"
        e.add_field(name="▸ History", value=txt, inline=False)
    if is_leader(member): e.add_field(name="Status", value="**LEADER**", inline=False)
    e.set_thumbnail(url=member.display_avatar.url); e.set_footer(text="🔹 Nexus")
    await interaction.response.send_message(embed=e, ephemeral=not public)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  M E M B E R   S E L E C T O R
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MemberSelector(View):
    def __init__(self, action, squad_role=None, squad_name=None, guild=None, page=1):
        super().__init__(timeout=180)
        self.action, self.squad_role, self.squad_name, self.guild, self.page = action, squad_role, squad_name, guild, page
        if action == "add_member": members = [m for m in guild.members if not m.bot and not get_member_squad(m, guild)[0]]
        elif action in ["remove_member", "set_main", "remove_main", "set_sub", "remove_sub"]:
            members = squad_role.members if squad_role else []
        elif action in ["give_guest", "remove_guest"]: members = [m for m in guild.members if not m.bot]
        else: members = []
        start = (page - 1) * 25; pm = members[start:start + 25]
        if not pm: return
        labels = {"add_member": "▸ Add", "remove_member": "▸ Remove", "set_main": "▸ Main",
                  "remove_main": "▸ Rm Main", "set_sub": "▸ Sub", "remove_sub": "▸ Rm Sub",
                  "give_guest": "▸ Guest", "remove_guest": "▸ Rm Guest"}
        sel = Select(placeholder=labels.get(action, "▸ Select"),
            options=[discord.SelectOption(label=m.display_name[:100], value=str(m.id), description=f"@{m.name[:50]}") for m in pm])
        sel.callback = self._sel; self.add_item(sel)
        if len(members) > 25:
            if page > 1:
                b = Button(label="◂", style=discord.ButtonStyle.secondary); b.callback = self._prev; self.add_item(b)
            if start + 25 < len(members):
                b = Button(label="▸", style=discord.ButtonStyle.secondary); b.callback = self._next; self.add_item(b)

    async def _prev(self, i):
        await i.response.edit_message(view=MemberSelector(self.action, self.squad_role, self.squad_name, self.guild, self.page - 1))
    async def _next(self, i):
        await i.response.edit_message(view=MemberSelector(self.action, self.squad_role, self.squad_name, self.guild, self.page + 1))

    async def _sel(self, interaction):
        m = self.guild.get_member(int(interaction.data["values"][0]))
        if not m: await interaction.response.edit_message(content="▸ Not found.", embed=None, view=None); return
        fn = getattr(self, f"_do_{self.action}", None)
        if fn: await fn(interaction, m)

    async def _do_add_member(self, i, m):
        old_r, _ = get_member_squad(m, self.guild); old_n = old_r.name if old_r else None
        for sn in bot_data["squads"]:
            r = discord.utils.get(self.guild.roles, name=sn)
            if r and r in m.roles: await m.remove_roles(r)
        await m.add_roles(self.squad_role)
        tag = bot_data["squads"].get(self.squad_name, {}).get("tag", "")
        await safe_nick(m, self.squad_role, tag); update_player_squad(m.id, self.squad_name, old_n)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Added", color=CLR_SUCCESS, description=f"{m.mention} → **{self.squad_name}**"), view=None)
        await log_action(self.guild, "🔹 Added", f"{i.user.mention} added {m.mention} to **{self.squad_name}**")

    async def _do_remove_member(self, i, m):
        info = bot_data["squads"][self.squad_name]
        if m.id in info.get("main_roster", []): info["main_roster"].remove(m.id)
        if m.id in info.get("subs", []): info["subs"].remove(m.id)
        await m.remove_roles(self.squad_role); await safe_nick(m, None, "")
        update_player_squad(m.id, "Free Agent", self.squad_name); save_data(bot_data)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Removed", color=CLR_DANGER, description=f"{m.mention} removed from **{self.squad_name}**"), view=None)

    async def _do_set_main(self, i, m):
        info = bot_data["squads"][self.squad_name]; mains = info.setdefault("main_roster", [])
        if len(mains) >= 5: await i.response.edit_message(content="▸ Full (5/5).", embed=None, view=None); return
        if m.id in mains: await i.response.edit_message(content="▸ Already main.", embed=None, view=None); return
        if m.id in info.get("subs", []): info["subs"].remove(m.id)
        mains.append(m.id); save_data(bot_data)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Main Set", color=CLR_SUCCESS, description=f"{m.mention} ({len(mains)}/5)"), view=None)

    async def _do_remove_main(self, i, m):
        info = bot_data["squads"][self.squad_name]; mains = info.get("main_roster", [])
        if m.id not in mains: await i.response.edit_message(content="▸ Not main.", embed=None, view=None); return
        mains.remove(m.id); save_data(bot_data)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Removed", color=CLR_WARN, description=f"{m.mention} off mains"), view=None)

    async def _do_set_sub(self, i, m):
        info = bot_data["squads"][self.squad_name]; subs = info.setdefault("subs", [])
        if len(subs) >= 3: await i.response.edit_message(content="▸ Full (3/3).", embed=None, view=None); return
        if m.id in subs: await i.response.edit_message(content="▸ Already sub.", embed=None, view=None); return
        if m.id in info.get("main_roster", []): info["main_roster"].remove(m.id)
        subs.append(m.id); save_data(bot_data)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Sub Set", color=CLR_SUCCESS, description=f"{m.mention} ({len(subs)}/3)"), view=None)

    async def _do_remove_sub(self, i, m):
        info = bot_data["squads"][self.squad_name]; subs = info.get("subs", [])
        if m.id not in subs: await i.response.edit_message(content="▸ Not sub.", embed=None, view=None); return
        subs.remove(m.id); save_data(bot_data)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Removed", color=CLR_WARN, description=f"{m.mention} off subs"), view=None)

    async def _do_give_guest(self, i, m):
        gn = bot_data["squads"].get(self.squad_name, {}).get("guest_role")
        if not gn: await i.response.edit_message(content="▸ No guest role.", embed=None, view=None); return
        gr = discord.utils.get(self.guild.roles, name=gn)
        if not gr: await i.response.edit_message(content=f"▸ `{gn}` missing.", embed=None, view=None); return
        await m.add_roles(gr)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Guest", color=CLR_SUCCESS, description=f"{m.mention} → guest"), view=None)

    async def _do_remove_guest(self, i, m):
        gn = bot_data["squads"].get(self.squad_name, {}).get("guest_role")
        if not gn: await i.response.edit_message(content="▸ No guest role.", embed=None, view=None); return
        gr = discord.utils.get(self.guild.roles, name=gn)
        if not gr or gr not in m.roles: await i.response.edit_message(content="▸ No guest on them.", embed=None, view=None); return
        await m.remove_roles(gr)
        await i.response.edit_message(embed=discord.Embed(title="🔹 Removed", color=CLR_WARN, description=f"{m.mention} guest revoked"), view=None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  P A N E L S
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class EditProfileFlow(View):
    """Step 1: Pick lane. Step 2: Pick rank. Step 3: Modal for IGN/ID."""
    def __init__(self, uid, sq):
        super().__init__(timeout=180)
        self.uid, self.sq = uid, sq
        self.lane = None

        sel = Select(placeholder="▸ Select your lane", options=[
            discord.SelectOption(label=l, value=l, description=LANE_ICONS.get(l, "")) for l in LANES
        ], row=0)

        async def lane_cb(interaction):
            if interaction.user.id != uid:
                await interaction.response.send_message("Not yours.", ephemeral=True); return
            self.lane = interaction.data["values"][0]
            await interaction.response.defer()

        sel.callback = lane_cb; self.add_item(sel)

        # Rank dropdown
        s2 = Select(placeholder="▸ Select your rank", options=[
            discord.SelectOption(label=r, value=r) for r in RANKS
        ], row=1)

        async def rank_cb(interaction):
            if interaction.user.id != uid:
                await interaction.response.send_message("Not yours.", ephemeral=True); return
            if not self.lane:
                await interaction.response.send_message("▸ Select lane first.", ephemeral=True); return
            rank = interaction.data["values"][0]
            if rank in STAR_RANKS:
                await interaction.response.send_modal(EditStarModal(uid, sq, self.lane, rank))
            else:
                existing = bot_data["players"].get(str(uid), {})
                await interaction.response.send_modal(ProfileEditModal(uid, sq, self.lane, rank, existing))

        s2.callback = rank_cb; self.add_item(s2)


class EditStarModal(Modal, title="🔹 Star Count"):
    """When editing profile with Glory/Immortal rank."""
    stars = TextInput(label="How many stars?", placeholder="e.g. 50, 130, 600", required=True, max_length=10)

    def __init__(self, uid, sq, lane, rank):
        super().__init__()
        self.uid, self.sq, self.lane, self.rank = uid, sq, lane, rank

    async def on_submit(self, interaction):
        try:
            star_num = int(self.stars.value.strip())
            full_rank = f"{self.rank} {star_num} stars"
        except ValueError:
            full_rank = self.rank
        existing = bot_data["players"].get(str(self.uid), {})
        await interaction.response.send_modal(ProfileEditModal(self.uid, self.sq, self.lane, full_rank, existing))


class RecruitLaneSel(View):
    def __init__(self, sq, tag, lid):
        super().__init__(timeout=180)
        sel = Select(placeholder="▸ Lane needed?", options=[
            discord.SelectOption(label=l, value=l, description=LANE_ICONS.get(l, "")) for l in LANES])
        async def cb(interaction):
            if interaction.user.id != lid:
                await interaction.response.send_message("Not yours.", ephemeral=True); return
            await interaction.response.send_modal(RecruitPostModal(sq, tag, interaction.data["values"][0], lid))
        sel.callback = cb; self.add_item(sel)


class MemberPanel(View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="My Squad", style=discord.ButtonStyle.primary, emoji="🔹", row=0)
    async def sq(self, i, b):
        role, tag = get_member_squad(i.user, i.guild)
        if not role: await i.response.send_message("▸ Not in a squad.", ephemeral=True); return
        await show_squad_info(i, role, role.name, tag, public=False)

    @discord.ui.button(label="My Profile", style=discord.ButtonStyle.primary, emoji="👤", row=0)
    async def pr(self, i, b): await show_profile(i, i.user, public=False)

    @discord.ui.button(label="Edit Profile", style=discord.ButtonStyle.secondary, emoji="✏️", row=0)
    async def ed(self, i, b):
        role, _ = get_member_squad(i.user, i.guild)
        sq = role.name if role else "Free Agent"
        v = EditProfileFlow(i.user.id, sq)
        e = discord.Embed(title="🔹 Edit Profile", color=CLR_ACCENT,
            description="**Step 1:** Select your lane\n**Step 2:** Select your rank\n*(Glory/Immortal will ask for star count)*\n**Step 3:** Fill in IGN & Game ID")
        await i.response.send_message(embed=e, view=v, ephemeral=True)

    @discord.ui.button(label="Find Team", style=discord.ButtonStyle.success, emoji="🔍", row=1)
    async def ft(self, i, b):
        p = bot_data["players"].get(str(i.user.id))
        if not p or not p.get("ingame_name"):
            await i.response.send_message("▸ Profile needed.", ephemeral=True); return
        await i.response.send_modal(FindTeamModal(i.user.id))

    @discord.ui.button(label="Apply to Squad", style=discord.ButtonStyle.success, emoji="📨", row=1)
    async def ap(self, i, b):
        if not bot_data["squads"]:
            await i.response.send_message("▸ No squads.", ephemeral=True); return
        p = bot_data["players"].get(str(i.user.id))
        if not p or not p.get("ingame_name"):
            await i.response.send_message("▸ Profile needed.", ephemeral=True); return
        v = ApplyDirectView(i.user.id)
        await i.response.send_message(embed=discord.Embed(title="🔹 Apply", description="Select squad.", color=CLR_RECRUIT), view=v, ephemeral=True)

    @discord.ui.button(label="Leave Squad", style=discord.ButtonStyle.danger, emoji="🚪", row=2)
    async def lv(self, i, b):
        role, _ = get_member_squad(i.user, i.guild)
        if not role: await i.response.send_message("▸ Not in squad.", ephemeral=True); return
        cv = View(timeout=60)
        async def yes(ci):
            if ci.user.id != i.user.id: await ci.response.send_message("Not yours.", ephemeral=True); return
            update_player_squad(i.user.id, None, role.name)
            await i.user.remove_roles(role); await safe_nick(i.user, None, None)
            await ci.response.send_message(f"▸ Left **{role.name}**.", ephemeral=True)
        async def no(ci): await ci.response.send_message("Cancelled.", ephemeral=True)
        yb = Button(label="Confirm", style=discord.ButtonStyle.danger); yb.callback = yes
        nb = Button(label="Cancel", style=discord.ButtonStyle.secondary); nb.callback = no
        cv.add_item(yb); cv.add_item(nb)
        await i.response.send_message(f"▸ Leave **{role.name}**?", view=cv, ephemeral=True)


class LeaderPanel(View):
    def __init__(self, sr, tag, sq):
        super().__init__(timeout=None); self.sr, self.tag, self.sq = sr, tag, sq

    @discord.ui.button(label="Add Member", emoji="＋", style=discord.ButtonStyle.success, row=0)
    async def add(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Add", color=CLR_ACCENT),
            view=MemberSelector("add_member", self.sr, self.sq, i.guild), ephemeral=True)

    @discord.ui.button(label="Remove Member", emoji="－", style=discord.ButtonStyle.danger, row=0)
    async def rm(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Remove", color=CLR_DANGER),
            view=MemberSelector("remove_member", self.sr, self.sq, i.guild), ephemeral=True)

    @discord.ui.button(label="View Squad", emoji="🔹", style=discord.ButtonStyle.primary, row=0)
    async def vs(self, i, b): await show_squad_info(i, self.sr, self.sq, self.tag, public=False)

    @discord.ui.button(label="Set Main", emoji="★", style=discord.ButtonStyle.primary, row=1)
    async def sm(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Main (5)", color=CLR_ACCENT),
            view=MemberSelector("set_main", self.sr, self.sq, i.guild), ephemeral=True)

    @discord.ui.button(label="Remove Main", emoji="☆", style=discord.ButtonStyle.secondary, row=1)
    async def rmm(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Rm Main", color=CLR_WARN),
            view=MemberSelector("remove_main", self.sr, self.sq, i.guild), ephemeral=True)

    @discord.ui.button(label="Set Sub", emoji="↻", style=discord.ButtonStyle.primary, row=2)
    async def ss(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Sub (3)", color=CLR_ACCENT),
            view=MemberSelector("set_sub", self.sr, self.sq, i.guild), ephemeral=True)

    @discord.ui.button(label="Remove Sub", emoji="×", style=discord.ButtonStyle.secondary, row=2)
    async def rms(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Rm Sub", color=CLR_WARN),
            view=MemberSelector("remove_sub", self.sr, self.sq, i.guild), ephemeral=True)

    @discord.ui.button(label="Post Recruitment", emoji="⊕", style=discord.ButtonStyle.success, row=3)
    async def pr(self, i, b):
        v = RecruitLaneSel(self.sq, self.tag, i.user.id)
        e = discord.Embed(title="🔹 Post Recruitment", color=CLR_RECRUIT,
            description=f"Select the lane you need.\nPost goes to `#{S('find_player_channel')}`.")
        await i.response.send_message(embed=e, view=v, ephemeral=True)

    @discord.ui.button(label="Search Player", emoji="⊙", style=discord.ButtonStyle.primary, row=3)
    async def sp(self, i, b):
        v = SearchFiltersView(self.sq, self.tag, i.user.id)
        e = discord.Embed(title="🔹 Search Player", color=CLR_ACCENT,
            description="**Lane** (required) + optional filters:\n▸ **Min Rank** — only show players at this rank or higher\n▸ **Gender** — filter by gender\n\nSelect filters then click **Search**.")
        await i.response.send_message(embed=e, view=v, ephemeral=True)

    @discord.ui.button(label="Give Guest", emoji="☉", style=discord.ButtonStyle.secondary, row=4)
    async def gg(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Guest", color=CLR_ACCENT),
            view=MemberSelector("give_guest", self.sr, self.sq, i.guild), ephemeral=True)

    @discord.ui.button(label="Remove Guest", emoji="⊘", style=discord.ButtonStyle.secondary, row=4)
    async def rg(self, i, b):
        await i.response.send_message(embed=discord.Embed(title="🔹 Rm Guest", color=CLR_WARN),
            view=MemberSelector("remove_guest", self.sr, self.sq, i.guild), ephemeral=True)


class ModPanel(View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Create Squad", style=discord.ButtonStyle.success, emoji="＋", row=0)
    async def cs(self, i, b): await i.response.send_modal(CreateSquadModal())

    @discord.ui.button(label="Delete Squad", style=discord.ButtonStyle.danger, emoji="－", row=0)
    async def ds(self, i, b): await i.response.send_modal(DeleteSquadModal())

    @discord.ui.button(label="Setup Verification", style=discord.ButtonStyle.primary, emoji="🔹", row=1)
    async def sv(self, i, b):
        await ensure_roles(i.guild); await send_verify_embed(i.guild)
        await i.response.send_message(embed=discord.Embed(title="🔹 Deployed", color=CLR_SUCCESS,
            description=f"Embed in `#{S('verification_channel')}`. Click **Grant Existing** before locking."), ephemeral=True)

    @discord.ui.button(label="Grant Existing", style=discord.ButtonStyle.success, emoji="✓", row=1)
    async def ge(self, i, b):
        await i.response.defer(ephemeral=True); guild = i.guild; await ensure_roles(guild)
        vr = discord.utils.get(guild.roles, name=S("verified_role"))
        ur = discord.utils.get(guild.roles, name=S("unverified_role"))
        if not vr: await i.followup.send("▸ Run Setup first.", ephemeral=True); return
        count = errors = 0
        for m in guild.members:
            if m.bot: continue
            if vr not in m.roles:
                try: await m.add_roles(vr, reason="Nexus 🔹"); count += 1; await asyncio.sleep(0.3)
                except: errors += 1
            if ur and ur in m.roles:
                try: await m.remove_roles(ur)
                except: pass
        e = discord.Embed(title="🔹 Granted", color=CLR_SUCCESS, description=(
            f"` ✓ ` **{count}** got `{S('verified_role')}`\n"
            + (f"` ! ` {errors} failed\n" if errors else "")
            + f"\n**Lock channels:**\n` 1 ` `@everyone` → Deny View\n` 2 ` `#{S('verification_channel')}` → `{S('unverified_role')}` View\n` 3 ` Other → `{S('verified_role')}` View+Send"))
        await i.followup.send(embed=e, ephemeral=True)

    @discord.ui.button(label="Configure Roles", style=discord.ButtonStyle.secondary, emoji="⚙", row=2)
    async def cr(self, i, b): await i.response.send_modal(ConfigModal())

    @discord.ui.button(label="Configure Channels", style=discord.ButtonStyle.secondary, emoji="⌘", row=2)
    async def cc(self, i, b): await i.response.send_modal(ConfigChannelsModal())

    @discord.ui.button(label="Download Backup", style=discord.ButtonStyle.secondary, emoji="↓", row=3)
    async def bk(self, i, b):
        if not os.path.exists(DATA_FILE):
            await i.response.send_message("▸ No data.", ephemeral=True); return
        try: await i.response.send_message("🔹 **Backup**",
            file=discord.File(DATA_FILE, filename=f"nexus_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"), ephemeral=True)
        except Exception as ex: await i.response.send_message(f"▸ {ex}", ephemeral=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  E V E N T S
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.event
async def on_ready():
    bot.add_view(VerifyButtonView())
    # Re-register persistent views
    for post in bot_data.get("recruitment_posts", []):
        if post["type"] == "find_player":
            bot.add_view(ApplyToSquadBtn(post["post_id"], post["squad_name"], post["squad_tag"], post["leader_id"]))
        elif post["type"] == "find_team":
            bot.add_view(RecruitBtn(post["post_id"], post["player_id"]))
    for inv in bot_data.get("tryout_invites", []):
        if inv.get("status") == "pending":
            bot.add_view(TryoutResponseView(inv["invite_id"]))

    await bot.tree.sync(); tag_sync.start()
    print(f"Online: {bot.user}\n🔹 Nexus ready")
    for guild in bot.guilds:
        await ensure_roles(guild); await send_verify_embed(guild)


@bot.event
async def on_member_join(member):
    if member.bot: return
    ur = discord.utils.get(member.guild.roles, name=S("unverified_role"))
    if ur:
        try: await member.add_roles(ur, reason="Nexus 🔹 New")
        except: pass
    try:
        await member.send(embed=discord.Embed(title="🔹 Welcome", color=CLR_ACCENT,
            description=f"Hey **{member.display_name}**, go to `#{S('verification_channel')}` and click **Verify**."))
    except: pass


@bot.event
async def on_member_update(before, after):
    role, tag = get_member_squad(after, after.guild)
    await bot.wait_until_ready(); await safe_nick(after, role, tag)


@tasks.loop(minutes=5)
async def tag_sync():
    for guild in bot.guilds:
        for m in guild.members:
            role, tag = get_member_squad(m, guild); await safe_nick(m, role, tag)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  S L A S H   C O M M A N D S
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.tree.command(name="panel", description="🔹 Member panel")
async def panel_cmd(interaction: discord.Interaction):
    v = MemberPanel()
    role, tag = get_member_squad(interaction.user, interaction.guild)
    sq = f"\n`{tag}` **{role.name}**" if role else "\nFree Agent"
    e = discord.Embed(title="🔹 Member Panel", color=CLR_MAIN,
        description=f"```\n{interaction.user.display_name}\n```{sq}")
    e.add_field(name="▸ Actions", value="🔹 Squad · 👤 Profile · ✏️ Edit\n🔍 Find Team · 📨 Apply · 🚪 Leave", inline=False)
    e.set_thumbnail(url=interaction.user.display_avatar.url); e.set_footer(text="🔹 Nexus")
    await interaction.response.send_message(embed=e, view=v, ephemeral=True)


@bot.tree.command(name="leader_panel", description="🔹 Leader panel")
async def leader_cmd(interaction: discord.Interaction):
    if not is_leader(interaction.user):
        await interaction.response.send_message("▸ Leaders only.", ephemeral=True); return
    role, tag = get_member_squad(interaction.user, interaction.guild)
    if not role:
        await interaction.response.send_message("▸ Must be in a squad.", ephemeral=True); return
    v = LeaderPanel(role, tag, role.name)
    e = discord.Embed(title=f"🔹 Leader ─ {role.name}",
        color=role.color if role.color != discord.Color.default() else CLR_ACCENT,
        description=f"```\n  {tag:^20}\n```")
    e.add_field(name=" Manage", value="＋/－ Members ·  Mains ·  Subs", inline=False)
    e.add_field(name=" Recruit", value=(
        f" **Post** → `#{S('find_player_channel')}`\n"
        " **Search** → filter by lane + rank + gender → send **tryout invite**"
    ), inline=False)
    e.add_field(name=" Access", value=" Guest roles", inline=False)
    e.set_footer(text="🔹 Nexus")
    await interaction.response.send_message(embed=e, view=v, ephemeral=True)


@bot.tree.command(name="mod_panel", description="🔹 Moderator panel")
async def mod_cmd(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        await interaction.response.send_message("▸ Moderators only.", ephemeral=True); return
    v = ModPanel(); s = bot_data["settings"]
    e = discord.Embed(title="Moderator Panel", color=CLR_MAIN, description="```\n  SERVER  CONTROLS\n```")
    e.add_field(name=" Squads", value="＋ Create  － Delete", inline=False)
    e.add_field(name=" Verification", value=" Deploy   Grant existing", inline=False)
    e.add_field(name=" Config", value=" Roles   Channels   Backup", inline=False)
    e.add_field(name=" Settings", value=(
        f"Verified: `{s.get('verified_role')}`\nUnverified: `{s.get('unverified_role')}`\n"
        f"Verify: `#{s.get('verification_channel')}`\nLogs: `#{s.get('log_channel')}`\n"
        f"Find Player: `#{s.get('find_player_channel')}`\nFind Team: `#{s.get('find_team_channel')}`"
    ), inline=False)
    e.set_footer(text=" Nexus")
    await interaction.response.send_message(embed=e, view=v, ephemeral=True)


@bot.tree.command(name="profile", description="🔹 View profile")
async def profile_cmd(interaction: discord.Interaction, member: discord.Member):
    await show_profile(interaction, member, public=True)


@bot.tree.command(name="help", description="🔹 Help")
async def help_cmd(interaction: discord.Interaction):
    e = discord.Embed(title="🔹 Nexus ─ Help", color=CLR_MAIN)
    e.add_field(name="▸ Everyone", value="`/panel` ─ Squad, profile, find team, apply\n`/profile @user` ─ View profile", inline=False)
    e.add_field(name="▸ Leaders", value="`/leader_panel` ─ Members, roster, recruitment, guests", inline=False)
    e.add_field(name="▸ Moderators", value="`/mod_panel` ─ Squads, verification, config, backup", inline=False)
    e.add_field(name="▸ Recruitment", value=(
        f"**Leaders** → Post in `#{S('find_player_channel')}` or search + send **tryout invite**\n"
        f"**Players** → Post in `#{S('find_team_channel')}` or apply directly\n"
        "Tryout invites give guest role on accept for voice channel access."
    ), inline=False)
    e.set_footer(text="🔹 /help")
    await interaction.response.send_message(embed=e, ephemeral=True)


bot.run(os.getenv("DISCORD_TOKEN"))
