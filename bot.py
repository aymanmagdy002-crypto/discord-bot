import discord
from discord.ext import commands
from discord.ui import Button, View, Select
import json
import os
import asyncio
from datetime import datetime

# ─────────────────────────────────────────
#  CONFIG – غيّر هذه القيم حسب سيرفرك
# ─────────────────────────────────────────
BOT_TOKEN = "MTA2NzQyNDU3MzgwMjQzMDU3Nw.GRW3d6.agl33WYnOMtESGxG2z-5JQAYEcT9sfOVwoE1Pk"
OFFERS_CHANNEL_ID   = 1494873426501046282  # ID روم العروض
TICKET_CATEGORY_ID  = 1494721365154988223  # ID كاتيجوري التكتات
MAX_ORDERS          = 3                    # عدد الطلبات قبل إغلاق العرض
DEFAULT_IMAGE       = "https://media.discordapp.net/ephemeral-attachments/1484741711421902859/1495837181573464105/default_order.png?ex=69f23e6f&is=69f0ecef&hm=e27a38448a6827849e3459259ed41ce16896bc86138a1ce1824d5a4a8954fa9b&=&format=webp&quality=lossless&width=300&height=300"

# ─────────────────────────────────────────
#  DATABASE (JSON بسيط)
# ─────────────────────────────────────────
DB_FILE = "offers_db.json"

def load_db() -> dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ─────────────────────────────────────────
#  BOT SETUP
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────
def parse_offer_text(text: str) -> tuple[str, str]:
    """
    يقسّم النص إلى تفاصيل أوردر وسعر.
    يبحث عن السعر بعد علامة + أو في سطر جديد يحتوي على كلمة السعر/price/$
    """
    price = "غير محدد"
    details = text.strip()

    # تقسيم بناءً على "+" أو سطر جديد
    separators = ["\n+", " + ", "\n"]
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            details = parts[0].strip()
            candidate = parts[1].strip()
            # تحقق إذا كان الجزء الثاني يشبه السعر
            if any(c.isdigit() for c in candidate) or any(
                kw in candidate.lower() for kw in ["$", "usd", "سعر", "price", "sar", "ريال"]
            ):
                price = candidate
            else:
                details = text.strip()  # fallback
            break

    # بحث مباشر عن سطر يبدأ بـ "السعر:" أو "price:"
    for line in text.split("\n"):
        line_lower = line.lower().strip()
        if line_lower.startswith(("السعر", "price:", "سعر:", "$")):
            price = line.strip()
            details = text.replace(line, "").strip()
            break

    return details, price


def build_offer_embed(
    owner: discord.Member,
    details: str,
    price: str,
    image_url: str,
    orders_count: int = 0,
    closed: bool = False,
    finished: bool = False,
) -> discord.Embed:
    if finished:
        color = discord.Color.orange()
        title = "🏁 Order Finished"
    elif closed:
        color = discord.Color.green()
        title = "✅ Order Completed"
    else:
        color = discord.Color.blurple()
        title = "🛒 New Offer"

    embed = discord.Embed(title=title, color=color, timestamp=datetime.utcnow())
    embed.set_author(name=owner.display_name, icon_url=owner.display_avatar.url)
    embed.add_field(name="📦 تفاصيل الطلب", value=details or "—", inline=False)
    embed.add_field(name="💰 السعر", value=price or "—", inline=True)
    embed.add_field(name="📊 الطلبات", value=f"{orders_count}/{MAX_ORDERS}", inline=True)
    embed.set_image(url=image_url)
    embed.set_footer(text=f"صاحب العرض: {owner} • {owner.id}")
    return embed


# ─────────────────────────────────────────
#  TICKET CLOSE CONFIRM VIEW
# ─────────────────────────────────────────
class CloseConfirmView(View):
    def __init__(self, ticket_channel: discord.TextChannel, client_id: int, owner_id: int):
        super().__init__(timeout=120)
        self.ticket_channel = ticket_channel
        self.client_id = client_id
        self.owner_id = owner_id

    @discord.ui.button(label="Close 🔒", style=discord.ButtonStyle.danger)
    async def confirm_close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()

        # حذف العميل من الروم
        try:
            client = interaction.guild.get_member(self.client_id)
            if client:
                await self.ticket_channel.set_permissions(client, overwrite=None)
        except Exception:
            pass

        # إرسال لوحة التحكم
        control_view = TicketControlView(
            ticket_channel=self.ticket_channel,
            client_id=self.client_id,
        )
        ctrl_embed = discord.Embed(
            title="🎟️ Ticket Closed",
            description="لوحة التحكم – اختر إجراءً:",
            color=discord.Color.red(),
        )
        await self.ticket_channel.send(embed=ctrl_embed, view=control_view)
        await interaction.message.delete()

    @discord.ui.button(label="Cancel ✖", style=discord.ButtonStyle.secondary)
    async def cancel_close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("❌ تم إلغاء الإغلاق.", ephemeral=True)
        await interaction.message.delete()


# ─────────────────────────────────────────
#  TICKET CONTROL VIEW  (Transcript / Open / Delete)
# ─────────────────────────────────────────
class TicketControlView(View):
    def __init__(self, ticket_channel: discord.TextChannel, client_id: int):
        super().__init__(timeout=None)
        self.ticket_channel = ticket_channel
        self.client_id = client_id

    @discord.ui.button(label="📜 Transcript", style=discord.ButtonStyle.secondary)
    async def transcript(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        messages = []
        async for msg in self.ticket_channel.history(limit=500, oldest_first=True):
            messages.append(f"[{msg.created_at.strftime('%H:%M:%S')}] {msg.author}: {msg.content}")
        content = "\n".join(messages) or "لا توجد رسائل."
        file_bytes = content.encode("utf-8")
        file = discord.File(
            fp=__import__("io").BytesIO(file_bytes),
            filename=f"transcript_{self.ticket_channel.name}.txt",
        )
        await interaction.followup.send("📜 الـ Transcript:", file=file, ephemeral=True)

    @discord.ui.button(label="🔓 Open", style=discord.ButtonStyle.success)
    async def reopen(self, interaction: discord.Interaction, button: Button):
        client = interaction.guild.get_member(self.client_id)
        if client:
            await self.ticket_channel.set_permissions(
                client,
                read_messages=True,
                send_messages=True,
            )
            await interaction.response.send_message(
                f"✅ تم إعادة {client.mention} للتكت.", ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ لم يُعثر على العضو.", ephemeral=True)

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.danger)
    async def delete_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🗑️ جارٍ حذف التكت...", ephemeral=True)
        await asyncio.sleep(2)
        await self.ticket_channel.delete(reason="Ticket deleted by staff")


# ─────────────────────────────────────────
#  TICKET VIEW  (Close button inside ticket)
# ─────────────────────────────────────────
class TicketView(View):
    def __init__(self, ticket_channel: discord.TextChannel, client_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.ticket_channel = ticket_channel
        self.client_id = client_id
        self.owner_id = owner_id

    @discord.ui.button(label="Close 🔒", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        confirm_view = CloseConfirmView(
            ticket_channel=self.ticket_channel,
            client_id=self.client_id,
            owner_id=self.owner_id,
        )
        await interaction.response.send_message(
            "⚠️ **Are you sure you would like to close this ticket?**",
            view=confirm_view,
        )


# ─────────────────────────────────────────
#  OFFER VIEW  (Order button)
# ─────────────────────────────────────────
class OfferView(View):
    def __init__(self, offer_msg_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.offer_msg_id = offer_msg_id
        self.owner_id = owner_id

    @discord.ui.button(
        label="Order",
        style=discord.ButtonStyle.success,
        emoji="🛒",
        custom_id="order_button",
    )
    async def order(self, interaction: discord.Interaction, button: Button):
        db = load_db()
        offer_key = str(self.offer_msg_id)

        # تأكد من وجود الأوفر في DB
        if offer_key not in db:
            await interaction.response.send_message(
                "❌ لم يُعثر على بيانات هذا العرض.", ephemeral=True
            )
            return

        offer = db[offer_key]

        # إذا كان العرض مغلقاً
        if offer.get("closed"):
            await interaction.response.send_message(
                "🔒 هذا العرض مغلق ولا يمكن طلبه.", ephemeral=True
            )
            return

        user_id = str(interaction.user.id)
        owner_id = str(self.owner_id)

        # صاحب العرض يضغط → إنهاء فوري
        if user_id == owner_id:
            offer["closed"] = True
            offer["finish_reason"] = "owner_finished"
            save_db(db)

            # تحديث الـ Embed
            try:
                channel = interaction.guild.get_channel(int(offer["channel_id"]))
                msg = await channel.fetch_message(self.offer_msg_id)
                owner = interaction.guild.get_member(int(owner_id))
                new_embed = build_offer_embed(
                    owner=owner,
                    details=offer["details"],
                    price=offer["price"],
                    image_url=offer["image_url"],
                    orders_count=len(offer.get("buyers", [])),
                    finished=True,
                )
                disabled_view = View()
                done_btn = Button(
                    label="Order Finished", style=discord.ButtonStyle.danger, disabled=True
                )
                disabled_view.add_item(done_btn)
                await msg.edit(embed=new_embed, view=disabled_view)
            except Exception:
                pass

            await interaction.response.send_message(
                "🏁 لقد أنهيتَ عرضك بنجاح.", ephemeral=True
            )
            return

        # العميل ضغط مرة ثانية
        if user_id in offer.get("ticket_opened_by", []):
            await interaction.response.send_message(
                "⚠️ لقد فتحتَ تكتاً لهذا العرض من قبل.", ephemeral=True
            )
            return

        # تسجيل الطلب
        if "buyers" not in offer:
            offer["buyers"] = []
        if "ticket_opened_by" not in offer:
            offer["ticket_opened_by"] = []

        if user_id not in offer["buyers"]:
            offer["buyers"].append(user_id)
        offer["ticket_opened_by"].append(user_id)

        # فتح التكت
        await interaction.response.defer(ephemeral=True)
        category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
        owner = interaction.guild.get_member(int(owner_id))

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
            reason=f"Ticket for offer {self.offer_msg_id}",
        )

        # رسالة داخل التكت
        ticket_embed = discord.Embed(
            title="🎟️ New Order Ticket",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow(),
        )
        ticket_embed.add_field(
            name="👤 العميل", value=interaction.user.mention, inline=True
        )
        ticket_embed.add_field(
            name="🏪 صاحب العرض", value=owner.mention if owner else f"<@{owner_id}>", inline=True
        )
        ticket_embed.add_field(name="📦 تفاصيل الطلب", value=offer["details"], inline=False)
        ticket_embed.add_field(name="💰 السعر", value=offer["price"], inline=True)
        ticket_embed.set_footer(text=f"Offer ID: {self.offer_msg_id}")

        ticket_view = TicketView(
            ticket_channel=ticket_channel,
            client_id=interaction.user.id,
            owner_id=int(owner_id),
        )

        mention_text = f"{interaction.user.mention}"
        if owner:
            mention_text += f" {owner.mention}"

        await ticket_channel.send(content=mention_text, embed=ticket_embed, view=ticket_view)

        # فحص إذا وصلنا لـ 3 مشترين → إغلاق العرض
        buyers_count = len(offer["buyers"])
        if buyers_count >= MAX_ORDERS:
            offer["closed"] = True
            save_db(db)

            try:
                channel = interaction.guild.get_channel(int(offer["channel_id"]))
                msg = await channel.fetch_message(self.offer_msg_id)
                new_embed = build_offer_embed(
                    owner=owner,
                    details=offer["details"],
                    price=offer["price"],
                    image_url=offer["image_url"],
                    orders_count=buyers_count,
                    closed=True,
                )
                disabled_view = View()
                done_btn = Button(
                    label="Order Completed ✅",
                    style=discord.ButtonStyle.success,
                    disabled=True,
                )
                disabled_view.add_item(done_btn)
                await msg.edit(embed=new_embed, view=disabled_view)
            except Exception:
                pass
        else:
            save_db(db)
            # تحديث عداد الطلبات في الـ Embed
            try:
                channel = interaction.guild.get_channel(int(offer["channel_id"]))
                msg = await channel.fetch_message(self.offer_msg_id)
                updated_embed = build_offer_embed(
                    owner=owner,
                    details=offer["details"],
                    price=offer["price"],
                    image_url=offer["image_url"],
                    orders_count=buyers_count,
                )
                await msg.edit(embed=updated_embed)
            except Exception:
                pass

        await interaction.followup.send(
            f"✅ تم فتح تكتك: {ticket_channel.mention}", ephemeral=True
        )


# ─────────────────────────────────────────
#  on_message — كشف العروض
# ─────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    # تجاهل رسائل البوت نفسه
    if message.author.bot:
        return

    # فقط في روم العروض المحدد
    if message.channel.id != OFFERS_CHANNEL_ID:
        await bot.process_commands(message)
        return

    # لا نتعامل مع رسائل فارغة
    content = message.content.strip()
    if not content:
        await bot.process_commands(message)
        return

    # استخراج الصورة إن وُجدت
    image_url = DEFAULT_IMAGE
    if message.attachments:
        att = message.attachments[0]
        if att.content_type and att.content_type.startswith("image"):
            image_url = att.url

    # تحليل النص
    details, price = parse_offer_text(content)

    # حذف رسالة العضو
    try:
        await message.delete()
    except discord.Forbidden:
        pass

    # بناء الـ Embed
    embed = build_offer_embed(
        owner=message.author,
        details=details,
        price=price,
        image_url=image_url,
        orders_count=0,
    )

    # إرسال الـ Embed مع الزر
    placeholder_view = View()  # سنحدّثه بعد إرسال الرسالة لنعرف الـ ID
    sent = await message.channel.send(
        content="@everyone",
        embed=embed,
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )

    # حفظ في DB
    db = load_db()
    db[str(sent.id)] = {
        "owner_id": str(message.author.id),
        "channel_id": str(message.channel.id),
        "details": details,
        "price": price,
        "image_url": image_url,
        "buyers": [],
        "ticket_opened_by": [],
        "closed": False,
    }
    save_db(db)

    # تحديث الرسالة بزر Order الفعلي
    offer_view = OfferView(offer_msg_id=sent.id, owner_id=message.author.id)
    await sent.edit(view=offer_view)


# ─────────────────────────────────────────
#  on_ready
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user} ({bot.user.id})")
    print(f"   Offers Channel : {OFFERS_CHANNEL_ID}")
    print(f"   Ticket Category: {TICKET_CATEGORY_ID}")


# ─────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────
bot.run(BOT_TOKEN)
