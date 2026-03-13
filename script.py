import json
import random
from rich.table import Table
from rich.console import Console

console = Console()

LINES_FILE = "lines.json"
ORDERS_FILE = "orders.json"
CONFIG_FILE = "config.json"

recent_orders = []
RECENT_LIMIT = 5

# --------------------
# LOAD DATA
# --------------------

def load_json(path):
    with open(path, "r", encoding="utf8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf8") as f:
        json.dump(data, f, indent=2)

lines = load_json(LINES_FILE)
orders = load_json(ORDERS_FILE)
config = load_json(CONFIG_FILE)

# --------------------
# HELPERS
# --------------------

def get_line_by_item(item_id):
    for l in lines:
        for t in l["Tiers"]:
            if t["ItemID"] == item_id:
                return l
    return None

def get_tier_by_item(item_id):
    for l in lines:
        for t in l["Tiers"]:
            if t["ItemID"] == item_id:
                return t["tier"]
    return None

# --------------------
# DIFFICULTY MODEL
# --------------------

def base_difficulty(tier):
    return 1.8 ** (tier - 1)

def generator_efficiency(line):
    g = line["Generator"]
    level = g["GeneratorLevel"]
    min_level = g["MinGeneratorLevel"]
    max_level = g["MaxGeneratorLevel"]
    if level <= min_level:
        return 1.0
    progress = (level - min_level) / max(1, max_level - min_level)
    max_eff = config.get("GeneratorEfficiencyMax", 2.0)
    return 1.0 + progress * (max_eff - 1.0)

def effective_difficulty(line_id, tier):
    line = next(l for l in lines if l["LineID"] == line_id)
    drop_rate = config["GeneratorDropRate"][line_id]
    efficiency = generator_efficiency(line)
    return base_difficulty(tier) / (drop_rate * efficiency)

def order_difficulty(order):
    total = 0
    for item in order["Items"]:
        line = get_line_by_item(item["ItemID"])
        tier = get_tier_by_item(item["ItemID"])
        total += effective_difficulty(line["LineID"], tier)
    return total

def active_orders_difficulty():
    return sum(order_difficulty(o) for o in orders)

def average_order_difficulty(): 
    if not orders: 
        return 0 
    return active_orders_difficulty() / len(orders) 

def max_order_difficulty(): 
    if not orders: 
        return 0 
    return max(order_difficulty(o) for o in orders)

# --------------------
# LINE STATS
# --------------------

def line_usage_count(line_id):
    count = 0
    for o in orders:
        for item in o["Items"]:
            if get_line_by_item(item["ItemID"])["LineID"] == line_id:
                count += 1
    return count

def has_high_tier(line_id):
    high = config["HighTier"][line_id]
    for o in orders:
        for item in o["Items"]:
            line = get_line_by_item(item["ItemID"])
            if line["LineID"] == line_id:
                tier = get_tier_by_item(item["ItemID"])
                if tier >= high:
                    return True
    return False

# --------------------
# RANDOM HELPERS
# --------------------

def weighted_choice(options, weights):
    total = sum(weights)
    r = random.uniform(0, total)
    upto = 0
    for opt, w in zip(options, weights):
        if upto + w >= r:
            return opt
        upto += w
    return options[-1]

def line_allowed_in_orders(line):
    return any(t.get("AllowedInOrders", True) for t in line["Tiers"])

def line_selection_weight(line):
    line_id = line["LineID"]
    generator_rate = config["GeneratorDropRate"][line_id]
    base_weight = config.get("LineBaseWeight", {}).get(line_id, 1.0)
    additional = line.get("AdditionalLineWeight", 1.0)
    repeat_mod = 1 / (1 + line_usage_count(line_id) * 0.5)
    high_mod = 0.6 if has_high_tier(line_id) else 1
    return base_weight * generator_rate * additional * repeat_mod * high_mod

# --------------------
# ITEM & ORDER GENERATION
# --------------------

def generate_item(exclude_items=None, exclude_lines=None):

    if exclude_items is None:
        exclude_items = set()

    if exclude_lines is None:
        exclude_lines = set()

    valid_lines = [
        l for l in lines
        if line_allowed_in_orders(l)
        and line_usage_count(l["LineID"]) < config["MaxItemsPerLine"]
        and l["LineID"] not in exclude_lines
    ]

    if not valid_lines:
        valid_lines = [l for l in lines if line_allowed_in_orders(l)]

    weights = [line_selection_weight(l) for l in valid_lines]

    line = weighted_choice(valid_lines, weights)

    # -----------------------------
    # LEVEL → MIN TIER
    # -----------------------------

    level_shift = config.get("LevelTierShift", 0)
    player_level = config.get("PlayerLevel", 1)

    min_tier = max(2, int(player_level * level_shift))

    # Orange можно делать низким
    if line["LineID"] == "Orange":
        min_tier = 1

    # -----------------------------
    # AVAILABLE TIERS
    # -----------------------------

    valid_tiers = [
        t for t in line["Tiers"]
        if (
            t["isOpened"]
            and t.get("AllowedInOrders", True)
            and t["ItemID"] not in exclude_items
            and t["tier"] >= min_tier
        )
    ]

    if not valid_tiers:
        valid_tiers = [
            t for t in line["Tiers"]
            if t["isOpened"] and t.get("AllowedInOrders", True)
        ]

    # -----------------------------
    # WEIGHTS
    # -----------------------------

    exponent = config.get("TierDifficultyExponent", 1.8)

    tier_weights = []

    for t in valid_tiers:

        tier = t["tier"]

        base_weight = 1 / (tier ** exponent)

        multiplier = config.get("TierWeightMultiplier", {}).get(
            str(tier), 1
        )

        tier_weights.append(base_weight * multiplier)

    tier = weighted_choice(valid_tiers, tier_weights)

    return tier["ItemID"], line["LineID"]

def generate_order():
    for _ in range(20):
        item_count = random.randint(1, 3)
        items = []
        used_items = set()
        used_lines = set()
        # исключаем уже существующие предметы в заказах
        all_existing_items = {i["ItemID"] for o in orders for i in o["Items"]}

        for _ in range(item_count):
            item, line_id = generate_item(used_items.union(all_existing_items), used_lines)
            items.append({"ItemID": item})
            used_items.add(item)
            used_lines.add(line_id)

        signature = tuple(sorted(i["ItemID"] for i in items))
        if signature not in recent_orders:
            order = {
                "OrderID": generate_unique_id(orders),
                "Items": items
            }
            recent_orders.append(signature)
            if len(recent_orders) > RECENT_LIMIT:
                recent_orders.pop(0)
            return order
    return None

def generate_unique_id(orders):
    existing_ids = {o["OrderID"] for o in orders}
    while True:
        new_id = str(random.randint(1, 9))
        if new_id not in existing_ids:
            return new_id

# --------------------
# ADD / COMPLETE ORDER
# --------------------

def add_order():
    if len(orders) >= config["MaxOrdersCount"]:
        print("Max orders reached")
        return
    new_order = generate_order()
    if new_order is None:
        print("Failed to generate order")
        return
    difficulty_sum = active_orders_difficulty() + order_difficulty(new_order)
    if difficulty_sum > config["MaxAllowedDifficultySum"]:
        print("Order rejected (difficulty overflow)")
        return
    orders.append(new_order)
    save_json(ORDERS_FILE, orders)
    print("New order added:", new_order["OrderID"])

def complete_order(order_id):
    global orders
    orders = [o for o in orders if o["OrderID"] != order_id]
    save_json(ORDERS_FILE, orders)
    print("Order completed:", order_id)

# --------------------
# SIMULATION
# --------------------

def run_simulation(n=5000):
    console.print("\n[bold yellow]Running simulation...[/bold yellow]\n")
    difficulty = []
    line_stats = {}
    tier_stats = {}
    for _ in range(n):
        order = generate_order()
        d = order_difficulty(order)
        difficulty.append(d)
        for i in order["Items"]:
            line = get_line_by_item(i["ItemID"])["LineID"]
            tier = get_tier_by_item(i["ItemID"])
            line_stats[line] = line_stats.get(line, 0) + 1
            tier_stats[tier] = tier_stats.get(tier, 0) + 1
    console.print("Average difficulty:", sum(difficulty) / len(difficulty))
    console.print("Max difficulty:", max(difficulty))
    table = Table(title="Line Distribution")
    table.add_column("Line")
    table.add_column("Count")
    for k, v in sorted(line_stats.items()):
        table.add_row(k, str(v))
    console.print(table)

# --------------------
# MAIN LOOP
# --------------------

while True:
    stats_table = Table(title="Economy Stats")
    stats_table.add_column("Metric")
    stats_table.add_column("Value", justify="right")
    stats_table.add_row("Total difficulty", f"{active_orders_difficulty():.2f}")
    stats_table.add_row("Average difficulty", f"{average_order_difficulty():.2f}")
    stats_table.add_row("Max difficulty", f"{max_order_difficulty():.2f}")
    console.print(stats_table)

    lines_table = Table(title="Line Selection Weights")
    lines_table.add_column("LineID")
    lines_table.add_column("Items in orders", justify="right")
    lines_table.add_column("Has High Tier", justify="center")
    lines_table.add_column("Weight", justify="right")
    for line in lines:
        if not line_allowed_in_orders(line):
            continue
        line_id = line["LineID"]
        items = line_usage_count(line_id)
        high = has_high_tier(line_id)
        weight = line_selection_weight(line)
        lines_table.add_row(line_id, str(items), str(high), f"{weight:.3f}")
    console.print(lines_table)

    orders_table = Table(title="Active Orders")
    orders_table.add_column("OrderID", justify="center")
    orders_table.add_column("Items")
    orders_table.add_column("Difficulty", justify="right")
    for o in orders:
        items = ", ".join(i["ItemID"] for i in o["Items"])
        diff = f"{order_difficulty(o):.2f}"
        orders_table.add_row(o["OrderID"], items, diff)
    console.print(orders_table)

    console.print(
        "\n[bold]Commands:[/bold]\n"
        "1 - add order\n"
        "2 <order_id> - complete order\n"
        "3 <order_id> - complete + add\n"
        "4 - simulation\n"
        "5 - exit\n"
    )
    cmd = input("command: ")
    parts = cmd.split()
    if parts[0] == "1":
        add_order()
    elif parts[0] == "2" and len(parts) > 1:
        complete_order(parts[1])
    elif parts[0] == "3" and len(parts) > 1:
        complete_order(parts[1])
        add_order()
    elif parts[0] == "4":
        run_simulation()
    elif parts[0] == "5":
        break
    else:
        console.print("[red]Unknown command[/red]")