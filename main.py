import flet as ft
import mysql.connector
from datetime import datetime, date, timedelta
import hashlib
import json
import os
import csv
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import requests
import base64
import re
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import sys
import os
from pathlib import Path
import appdirs
import asyncio
import threading
import tempfile

# ---------------------------- 数据库配置 ----------------------------
DB_HOST = os.getenv("DB_HOST", "240e:338:4a26:f3b1::84")
DB_PORT = int(os.getenv("DB_PORT", 13306))
DB_USER = os.getenv("DB_USER", "ipv6user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_DATABASE = os.getenv("DB_DATABASE", "jiuchengerp")
CONFIG_FILE = "server_config.json"

PERMISSIONS = ["首页", "销售", "入库", "运输", "安装", "库存", "更多"]
PERMISSION_ICONS = {
    "首页": ft.Icons.HOME,
    "销售": ft.Icons.SHOPPING_CART,
    "入库": ft.Icons.INVENTORY,
    "运输": ft.Icons.LOCAL_SHIPPING,
    "安装": ft.Icons.HANDYMAN,
    "库存": ft.Icons.DATASET,
    "更多": ft.Icons.SETTINGS,
}

def get_asset_path(filename):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "assets", filename)

def get_config_dir():
    if getattr(sys, 'frozen', False):
        if os.name == 'nt':
            config_dir = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'jiuchengerp')
        else:
            config_dir = os.path.join(os.path.expanduser('~'), '.config', 'jiuchengerp')
    else:
        config_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(config_dir, exist_ok=True)
    return config_dir

CONFIG_DIR = get_config_dir()
CONFIG_FILE = os.path.join(CONFIG_DIR, 'server_config.json')

DEFAULT_HOST = os.getenv("DB_HOST", "240e:338:4a26:f3b1::84")
DEFAULT_PORT = int(os.getenv("DB_PORT", 13306))
DEFAULT_USER = os.getenv("DB_USER", "ipv6user")
DEFAULT_PASSWORD = os.getenv("DB_PASSWORD", "")
DEFAULT_DATABASE = os.getenv("DB_DATABASE", "jiuchengerp")

DB_HOST = DEFAULT_HOST
DB_PORT = DEFAULT_PORT
DB_USER = DEFAULT_USER
DB_PASSWORD = DEFAULT_PASSWORD
DB_DATABASE = DEFAULT_DATABASE

def load_server_config():
    global DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_DATABASE
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                DB_HOST = cfg.get("DB_HOST", DEFAULT_HOST)
                DB_PORT = int(cfg.get("DB_PORT", DEFAULT_PORT))
                DB_USER = cfg.get("DB_USER", DEFAULT_USER)
                DB_PASSWORD = cfg.get("DB_PASSWORD", DEFAULT_PASSWORD)
                DB_DATABASE = cfg.get("DB_DATABASE", DEFAULT_DATABASE)
        except Exception as e:
            print(f"读取配置文件失败: {e}，使用默认值")
    else:
        save_server_config(DEFAULT_HOST, DEFAULT_PORT, DEFAULT_USER, DEFAULT_PASSWORD, DEFAULT_DATABASE)

def save_server_config(host, port, user, pwd, db):
    cfg = {
        "DB_HOST": host,
        "DB_PORT": port,
        "DB_USER": user,
        "DB_PASSWORD": pwd,
        "DB_DATABASE": db
    }
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

load_server_config()

def get_db_conn():
    try:
        return mysql.connector.connect(
            host=DB_HOST, port=int(DB_PORT), user=DB_USER,
            password=DB_PASSWORD, database=DB_DATABASE,
            use_pure=True, connect_timeout=5)
    except Exception as e:
        print("数据库错误:", e)
        return None

def md5_pwd(pwd):
    return hashlib.md5(pwd.encode("utf-8")).hexdigest()

def gen_order_no():
    year = date.today().strftime("%Y")
    conn = get_db_conn()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(order_no) FROM sale_main WHERE order_no LIKE %s", (f"{year}%",))
        max_no = cur.fetchone()[0]
        conn.close()
        seq = int(max_no[4:]) + 1 if max_no else 1
        return f"{year}{seq:04d}"
    return f"{year}0001"

def gen_invoice_no():
    return f"INV{date.today().strftime('%Y%m%d')}{int(datetime.now().timestamp()) % 10000:04d}"

def resource_path(relative_path):
    try:
        return os.path.join(sys._MEIPASS, relative_path)
    except:
        return os.path.join(os.path.abspath("."), relative_path)

# ===================== 辅助函数：显示弹窗 =====================
def show_alert(page: ft.Page, title, content, on_ok=None):
    to_remove = []
    for ctrl in page.overlay:
        if isinstance(ctrl, ft.AlertDialog):
            ctrl.open = False
            to_remove.append(ctrl)
    page.update()
    for d in to_remove:
        page.overlay.remove(d)
    page.update()

    def handle_ok(e):
        dlg.open = False
        page.update()
        threading.Timer(0.1,
                        lambda: (page.overlay.remove(dlg), page.update()) if dlg in page.overlay else None).start()
        if on_ok:
            on_ok(e)

    dlg = ft.AlertDialog(
        title=ft.Text(title, weight=ft.FontWeight.BOLD),
        content=ft.Text(content),
        modal=True,
        actions=[ft.TextButton("确定", on_click=handle_ok)]
    )
    page.overlay.append(dlg)
    dlg.open = True
    page.update()

# ===================== 通用扫码函数（仅返回识别码，不查询产品） =====================
def scan_barcode_only(page: ft.Page, callback, title="扫码识别"):
    """
    打开一个扫码弹窗，识别后仅返回码字符串给callback
    """
    dialog_ref = None
    tip_text = ft.Text("对准条码自动识别", size=12, text_align=ft.TextAlign.CENTER)

    def handle_scan_success(code):
        tip_text.value = f"识别成功：{code}"
        tip_text.color = ft.Colors.GREEN
        page.update()
        callback(code)
        def close_dialog():
            if dialog_ref and dialog_ref.open:
                dialog_ref.open = False
            if dialog_ref in page.overlay:
                page.overlay.remove(dialog_ref)
            page.update()
        threading.Timer(0.8, close_dialog).start()

    def on_scan_result(e):
        # 处理多码选择
        if e.barcodes and len(e.barcodes) > 1:
            # 弹出选择对话框让用户选择
            def choose_barcode(code):
                # 关闭选择弹窗，然后处理识别结果
                choice_dlg.open = False
                page.update()
                handle_scan_success(code)

            items = []
            for bc in e.barcodes:
                items.append(ft.ListTile(
                    title=ft.Text(bc.data.decode('utf-8', errors='ignore')),
                    on_click=lambda _, code=bc.data.decode('utf-8', errors='ignore'): choose_barcode(code)
                ))
            choice_dlg = ft.AlertDialog(
                title=ft.Text("检测到多个条码，请选择"),
                content=ft.Column(items, scroll=ft.ScrollMode.AUTO, height=200),
                actions=[ft.TextButton("取消", on_click=lambda _: (setattr(choice_dlg, 'open', False), page.update()))]
            )
            page.overlay.append(choice_dlg)
            choice_dlg.open = True
            page.update()
            return

        if e.data:
            scanner.disabled = True
            page.update()
            handle_scan_success(e.data)

    scanner = ft.BarcodeScanner(
        on_scan=on_scan_result,
        width=min(320, page.window_width - 40) if page.window_width else 300,
        height=320,
        resolution=ft.BarcodeScannerResolution.MEDIUM
    )

    def open_album(e):
        tip_text.value = ""
        page.update()
        scanner.pick_image()

    def close_dialog(e):
        if dialog_ref:
            dialog_ref.open = False
            if dialog_ref in page.overlay:
                page.overlay.remove(dialog_ref)
        page.update()

    dialog_content = ft.Column(
        [
            scanner,
            tip_text
        ],
        width=min(320, page.window_width - 40) if page.window_width else 300,
        spacing=8,
        scroll=ft.ScrollMode.AUTO
    )

    dialog_ref = ft.AlertDialog(
        title=ft.Text(title),
        content=dialog_content,
        modal=True,
        content_padding=ft.Padding(12, 10, 12, 10),
        actions=[
            ft.TextButton("相册识别", on_click=open_album),
            ft.TextButton("关闭", on_click=close_dialog)
        ]
    )

    if page.platform == "android":
        try:
            page.request_permission("android.permission.CAMERA")
        except:
            pass

    page.overlay.append(dialog_ref)
    dialog_ref.open = True
    page.update()

# ===================== 产品查询（原 scan_barcode_from_image 保持不变，但内部调用修改） =====================
def scan_barcode_from_image(page: ft.Page, on_match_success):
    """
    原有扫码函数，用于产品匹配，内部使用BarcodeScanner
    """
    # 复用 scan_barcode_only 逻辑，但增加产品查询
    def handle_result(code):
        match_data = query_product_by_code(code)
        on_match_success(code, match_data)

    scan_barcode_only(page, handle_result, title="扫码识别商品")

def get_product_by_model(model):
    conn = get_db_conn()
    if not conn:
        return None
    cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT code, model, spec, factory, category, piece, price,
                          union_subsidy, gov_subsidy, old_discount
                   FROM base_product WHERE model=%s""", (model,))
    row = cur.fetchone()
    conn.close()
    return row

def query_product_by_code(code):
    conn = get_db_conn()
    if not conn:
        return None
    cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT code, model, spec, factory, category, piece, price,
                          union_subsidy, gov_subsidy, old_discount
                   FROM base_product WHERE code=%s""", (code,))
    row = cur.fetchone()
    conn.close()
    return row

def add_product_from_scan(page, code, callback):
    def save_product(e):
        model = model_input.value.strip()
        if not model:
            page.snack_bar = ft.SnackBar(ft.Text("型号不能为空"))
            page.snack_bar.open = True
            return
        try:
            price = float(price_input.value or 0)
            union = float(union_input.value or 0)
            gov = float(gov_input.value or 0)
            old = float(old_input.value or 0)
        except:
            page.snack_bar = ft.SnackBar(ft.Text("价格/补贴请输入数字"))
            page.snack_bar.open = True
            return
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""INSERT INTO base_product
                        (code, model, spec, factory, category, piece, price,
                         union_subsidy, gov_subsidy, old_discount)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (code, model, spec_input.value, factory_input.value,
                         category_input.value, piece_input.value, price,
                         union, gov, old))
            conn.commit()
            page.snack_bar = ft.SnackBar(ft.Text("产品添加成功"))
            page.snack_bar.open = True
            dialog.open = False
            page.update()
            callback(model)
        except Exception as ex:
            page.snack_bar = ft.SnackBar(ft.Text(f"添加失败: {ex}"))
            page.snack_bar.open = True
        finally:
            conn.close()

    model_input = ft.TextField(label="型号*", width=250)
    code_input = ft.TextField(label="69码", value=code, width=250, read_only=True)
    factory_input = ft.TextField(label="品牌", width=250)
    category_input = ft.TextField(label="品类", width=250)
    spec_input = ft.TextField(label="规格", width=250)
    piece_input = ft.TextField(label="单位", value="台", width=250)
    price_input = ft.TextField(label="单价", value="0", width=250)
    union_input = ft.TextField(label="工会补贴%", value="0", width=250)
    gov_input = ft.TextField(label="国家补贴%", value="0", width=250)
    old_input = ft.TextField(label="旧机折扣", value="0", width=250)

    dialog = ft.AlertDialog(
        title=ft.Text("新增产品"),
        content=ft.Column([model_input, code_input, factory_input, category_input,
                           spec_input, piece_input, price_input, union_input,
                           gov_input, old_input], tight=True, spacing=8,
                          scroll=ft.ScrollMode.AUTO),
        actions=[ft.TextButton("保存", on_click=save_product),
                 ft.TextButton("取消", on_click=lambda e: setattr(dialog, 'open', False))]
    )
    page.overlay.append(dialog)
    dialog.open = True
    page.update()

# ---------------------------- PDF订单生成（未变） ----------------------------
def generate_pdf_order(order_no, items, cust_name, phone, full_addr, send_date, total):
    try:
        pdf_path = f"订单_{order_no}.pdf"
        c = canvas.Canvas(pdf_path, pagesize=A4)
        width, height = A4

        c.setFont("Helvetica-Bold", 18)
        c.drawString(50, height - 50, "销售订单")
        c.setFont("Helvetica", 12)
        y = height - 80
        c.drawString(50, y, f"订单号: {order_no}")
        y -= 20
        c.drawString(50, y, f"客户: {cust_name}  电话: {phone}")
        y -= 20
        c.drawString(50, y, f"地址: {full_addr}")
        y -= 20
        c.drawString(50, y, f"送货日期: {send_date}")
        y -= 30

        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, "序号")
        c.drawString(100, y, "型号")
        c.drawString(250, y, "数量")
        c.drawString(320, y, "单价")
        c.drawString(400, y, "总价")
        y -= 20
        c.setFont("Helvetica", 10)
        for idx, it in enumerate(items, 1):
            c.drawString(50, y, str(idx))
            c.drawString(100, y, it["model"])
            c.drawString(250, y, str(it["qty"]))
            c.drawString(320, y, f"{it['price']:.2f}")
            c.drawString(400, y, f"{it['total']:.2f}")
            y -= 20

        y -= 20
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, f"合计: {total:.2f} 元")

        try:
            stamp_path = resource_path("stamp.png")
            if os.path.exists(stamp_path):
                img = ImageReader(stamp_path)
                c.drawImage(img, width - 150, 50, width=100, height=100, mask='auto')
        except:
            pass

        c.save()
        if os.name == 'nt':
            os.startfile(pdf_path)
        return pdf_path
    except Exception as e:
        print(f"生成PDF失败: {e}")
        return None

# ---------------------------- Flet 应用 ----------------------------
def main(page: ft.Page):
    page.title = "玖诚电器ERP"
    page.window_icon = resource_path("logo.ico")
    page.icon = resource_path("login_bg.png")
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.spacing = 0
    page.scroll = ft.ScrollMode.AUTO
    page.window_width = 400
    page.window_height = 700
    page.window_resizable = True

    current_user = None
    main_content = ft.Column(expand=True, spacing=0, scroll=ft.ScrollMode.AUTO)

    # ---------- 全屏配置覆盖层 ----------
    config_overlay = ft.Container(
        content=ft.Column(
            [
                ft.Text("数据库服务器配置", size=20, weight=ft.FontWeight.BOLD),
                ft.Divider(height=10),
                ft.TextField(label="服务器地址（支持IPv6）", value=DB_HOST, width=300),
                ft.TextField(label="端口", value=str(DB_PORT), width=300),
                ft.TextField(label="数据库用户名", value=DB_USER, width=300),
                ft.TextField(label="数据库密码", password=True, can_reveal_password=True, value=DB_PASSWORD, width=300),
                ft.TextField(label="数据库名称", value=DB_DATABASE, width=300),
                ft.Divider(height=10),
                ft.Column(
                    [
                        ft.Button("读取主机IPv6", on_click=lambda e: read_ipv6(page), width=260),
                        ft.Button("测试连接", on_click=lambda e: test_conn(), width=260),
                        ft.Button("保存并重启", on_click=lambda e: save_and_restart(), width=260),
                        ft.OutlinedButton("取消", on_click=lambda e: hide_config(), width=260),
                    ],
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=ft.Colors.WHITE,
        padding=20,
        expand=True,
        visible=False,
    )

    def request_all_permissions():
        if page.platform == "android":
            try:
                page.request_permission("android.permission.CAMERA")
                page.request_permission("android.permission.READ_MEDIA_IMAGES")
                page.request_permission("android.permission.READ_EXTERNAL_STORAGE")
                page.request_permission("android.permission.WRITE_EXTERNAL_STORAGE")
            except Exception as e:
                print(f"权限申请异常: {e}")

    request_all_permissions()

    def get_field_width(page, ratio=1, subtract=40):
        base_width = page.window_width if page.window_width else 360
        calc_width = (base_width - subtract) / ratio
        return max(100, round(calc_width))

    def get_fields():
        controls = config_overlay.content.controls
        return {
            "host": controls[2],
            "port": controls[3],
            "user": controls[4],
            "pwd": controls[5],
            "db": controls[6],
        }

    def read_ipv6(page):
        input_tf = ft.TextField(
            label="读取码",
            width=280,
            autofocus=True,
            on_change=lambda e: (setattr(error_tip, "value", ""), page.update())
        )
        error_tip = ft.Text("", size=12, text_align=ft.TextAlign.CENTER)

        def fetch_data(key):
            try:
                web_url = f"https://textdb.online/{key}"
                resp = requests.get(web_url, timeout=10)
                raw_text = resp.text.strip()
                decoded = raw_text
                try:
                    decoded = base64.b64decode(raw_text).decode("utf-8")
                except Exception:
                    decoded = raw_text

                if resp.status_code != 200:
                    error_tip.value = f"读取失败：HTTP {resp.status_code}"
                    error_tip.color = ft.Colors.RED
                    page.update()
                    return
                if not raw_text:
                    error_tip.value = "读取码对应数据为空"
                    error_tip.color = ft.Colors.RED
                    page.update()
                    return

                if ":" in decoded:
                    fields = get_fields()
                    fields["host"].value = decoded
                    page.update()
                    error_tip.value = f"已填入IPv6: {decoded}"
                    error_tip.color = ft.Colors.GREEN
                    page.update()
                else:
                    error_tip.value = "内容不是有效IPv6地址"
                    error_tip.color = ft.Colors.RED
                    page.update()
            except Exception as ex:
                error_tip.value = f"读取失败: {str(ex)[:50]}"
                error_tip.color = ft.Colors.RED
                page.update()

        def on_submit(e):
            read_key = input_tf.value.strip()
            if not read_key:
                error_tip.value = "请输入读取码"
                error_tip.color = ft.Colors.RED
                page.update()
                return
            error_tip.value = "读取ipv6中，请稍等……"
            error_tip.color = ft.Colors.BLUE
            page.update()
            threading.Thread(target=fetch_data, args=(read_key,), daemon=True).start()

        def on_cancel(e):
            input_dlg.open = False
            page.update()
            def clean_dlg():
                if input_dlg in page.overlay:
                    page.overlay.remove(input_dlg)
                    page.update()
            threading.Timer(0.1, clean_dlg).start()

        dialog_content = ft.Container(
            content=ft.Stack([
                input_tf,
                ft.Row([error_tip], alignment=ft.MainAxisAlignment.CENTER, top=78)
            ]),
            width=280,
            height=95
        )

        input_dlg = ft.AlertDialog(
            title=ft.Text("请输入读取码"),
            content=dialog_content,
            modal=True,
            content_padding=ft.Padding(16, 10, 16, 8),
            actions=[
                ft.TextButton("确定", on_click=on_submit),
                ft.TextButton("取消", on_click=on_cancel),
            ]
        )
        page.overlay.append(input_dlg)
        input_dlg.open = True
        page.update()

    def test_conn():
        fields = get_fields()
        host = fields["host"].value.strip()
        port_str = fields["port"].value.strip()
        user = fields["user"].value.strip()
        pwd = fields["pwd"].value.strip()
        db = fields["db"].value.strip()

        if not host or not port_str or not user or not db:
            show_alert(page,"提示", "请填写完整的连接信息")
            return

        try:
            port = int(port_str)
            conn = mysql.connector.connect(
                host=host, port=port, user=user,
                password=pwd, database=db,
                use_pure=True, connect_timeout=3
            )
            conn.close()
            show_alert(page,"成功", "✅ 连接成功")
        except Exception as ex:
            show_alert(page,"错误", f"❌ 连接失败: {str(ex)[:50]}")

    def save_and_restart():
        nonlocal current_user
        fields = get_fields()
        host = fields["host"].value.strip()
        port = int(fields["port"].value)
        user = fields["user"].value.strip()
        pwd = fields["pwd"].value.strip()
        db = fields["db"].value.strip()

        save_server_config(host, port, user, pwd, db)
        global DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_DATABASE
        DB_HOST = host
        DB_PORT = port
        DB_USER = user
        DB_PASSWORD = pwd
        DB_DATABASE = db

        config_overlay.visible = False
        page.update()

        def on_ok(e):
            page.dialog.open = False
            page.update()
            page.dialog = None
            current_user = None
            page.controls.clear()
            page.add(login_container)
            page.update()
        show_alert(page, "配置保存成功", "数据库配置已更新，请重新登录")

    def show_config(e):
        config_overlay.visible = True
        fields = get_fields()
        fields["host"].value = DB_HOST
        fields["port"].value = str(DB_PORT)
        fields["user"].value = DB_USER
        fields["pwd"].value = DB_PASSWORD
        fields["db"].value = DB_DATABASE
        page.update()

    def hide_config():
        config_overlay.visible = False
        page.update()

    # ---------- 登录界面 ----------
    def do_login(e):
        nonlocal current_user
        uname = username_input.value.strip()
        pwd = password_input.value.strip()
        if not uname or not pwd:
            show_alert(page, "提示", "请输入用户名和密码")
            return
        conn = get_db_conn()
        if not conn:
            show_alert(page, "提示", "数据库连接失败，请检查服务器配置")
            return
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id,username,real_name,role,permissions,expire_date FROM users WHERE username=%s AND password=%s",
                    (uname, md5_pwd(pwd)))
        user = cur.fetchone()
        conn.close()
        if user:
            expire = user.get("expire_date")
            if expire and expire < date.today():
                show_alert(page, "提示", "用户权限已过期，请联系管理员")
                return
            current_user = user
            build_main_ui()
        else:
            show_alert(page, "提示", "用户名或密码错误")

    username_input = ft.TextField(label="用户名", width=300, autofocus=True)
    password_input = ft.TextField(label="密码", password=True, can_reveal_password=True, width=300)
    login_btn = ft.Button("登录", on_click=do_login, width=300)
    settings_btn = ft.IconButton(ft.Icons.SETTINGS, on_click=show_config)

    login_column = ft.Column(
        [
            ft.Row([ft.Container(expand=True), settings_btn], alignment=ft.MainAxisAlignment.END),
            ft.Container(height=20),
            ft.Text("玖诚电器ERP", size=32, weight=ft.FontWeight.BOLD),
            ft.Image(src=get_asset_path("login_bg.png"), width=100, height=100),
            username_input,
            password_input,
            login_btn,
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=15,
    )

    login_container = ft.Container(
        content=login_column,
        alignment=ft.Alignment(0, 0),
        expand=True,
    )

    page.add(
        ft.Stack(
            [
                login_container,
                config_overlay,
            ],
            expand=True,
        )
    )
    page.update()

    # ---------- 主界面框架 ----------
    def build_main_ui():
        page.controls.clear()
        def on_window_resize(e):
            page.update()
        page.on_window_resize = on_window_resize

        appbar = ft.AppBar(
            title=ft.Text("玖诚电器ERP"),
            center_title=False,
            bgcolor=ft.Colors.SURFACE,
            actions=[ft.IconButton(ft.Icons.PERSON, on_click=lambda e: show_profile())]
        )

        if current_user and current_user.get("role") == "超级管理员":
            perm_list = PERMISSIONS
        else:
            perm_list = (current_user.get("permissions") or "").split(",") if current_user else []
            if not perm_list:
                perm_list = ["首页"]
            perm_list = [p for p in perm_list if p in PERMISSIONS]
            if not perm_list:
                perm_list = ["首页"]

        destinations = []
        for p in PERMISSIONS:
            if p in perm_list:
                destinations.append(
                    ft.NavigationBarDestination(
                        icon=PERMISSION_ICONS.get(p, ft.Icons.HELP),
                        label=p
                    )
                )

        nav_bar = ft.NavigationBar(
            destinations=destinations,
            on_change=on_nav_change,
            elevation=8
        )

        main_content.expand = True
        main_content.scroll = ft.ScrollMode.AUTO

        main_layout = ft.Column(
            [
                appbar,
                main_content,
                nav_bar,
            ],
            spacing=0,
            expand=True,
        )
        page.add(main_layout)
        show_home()

    def on_nav_change(e):
        selected_index = e.control.selected_index
        if selected_index < len(e.control.destinations):
            label = e.control.destinations[selected_index].label
            if label == "首页":
                show_home()
            elif label == "销售":
                show_sale()
            elif label == "入库":
                show_inbound()
            elif label == "运输":
                show_transport()
            elif label == "安装":
                show_install()
            elif label == "库存":
                show_stock()
            elif label == "更多":
                show_more_menu()

    def get_file_from_db(file_type, biz_no):
        conn = get_db_conn()
        if not conn:
            return None
        cur = conn.cursor()
        cur.execute(
            "SELECT file_data FROM erp_files WHERE file_type=%s AND biz_no=%s ORDER BY id DESC LIMIT 1",
            (file_type, biz_no)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    # ---------------------------- 首页 ----------------------------
    def show_home():
        main_content.controls.clear()
        conn = get_db_conn()
        if not conn:
            main_content.controls.append(ft.Text("无法连接数据库"))
            page.update()
            return
        cur = conn.cursor()
        cur.execute("SELECT SUM(s_qty) FROM stock_now")
        total_stock = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(DISTINCT order_no) FROM sale_main WHERE MONTH(order_date)=MONTH(CURDATE())")
        month_sales = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM transport WHERE status='待出库'")
        pending_trans = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM install WHERE status='待安装'")
        pending_install = cur.fetchone()[0] or 0
        conn.close()

        cards_data = [
            ("📦", "当前库存", str(total_stock), ft.Colors.BLUE),
            ("📊", "本月销售单数", str(month_sales), ft.Colors.GREEN),
            ("🚚", "待出库订单", str(pending_trans), ft.Colors.ORANGE),
            ("🔧", "待安装订单", str(pending_install), ft.Colors.RED),
        ]

        padding = 16
        spacing = 12
        card_width = (page.window_width - padding * 2 - spacing) // 2 if page.window_width else 180

        cards_row = ft.Row(
            wrap=True,
            spacing=spacing,
            run_spacing=spacing,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        )

        for icon, label, value, color in cards_data:
            card = ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(icon, size=30),
                            ft.Text(value, size=28, weight=ft.FontWeight.BOLD, color=color),
                            ft.Text(label, size=12, color=ft.Colors.GREY_700),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=5,
                    ),
                    alignment=ft.Alignment(0, 0),
                    padding=15,
                    width=card_width,
                    height=card_width * 1.1,
                ),
                elevation=3,
            )
            cards_row.controls.append(card)

        refresh_btn = ft.Button(
            "刷新数据",
            icon=ft.Icons.REFRESH,
            on_click=lambda e: show_home(),
            width=200,
        )

        main_content.controls.append(
            ft.Column(
                [
                    cards_row,
                    ft.Container(height=20),
                    ft.Row([refresh_btn], alignment=ft.MainAxisAlignment.CENTER),
                ],
                spacing=0,
                scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
        page.update()

    # ---------------------------- 销售订单（完整版） ----------------------------
    def show_sale():
        main_content.controls.clear()
        order_no = gen_order_no()
        current_county = ""

        county_list = []
        conn = get_db_conn()
        if conn:
            try:
                cur = conn.cursor()
                sql = "SELECT county FROM base_address WHERE TRIM(city) = %s GROUP BY county ORDER BY MIN(id)"
                cur.execute(sql, ("铜仁市",))
                county_list = [row[0].strip() for row in cur.fetchall()]
            except Exception as ex:
                print(f"加载区县失败: {ex}")
            finally:
                conn.close()
        if not county_list:
            county_list = [
                "碧江区", "万山区", "松桃苗族自治县", "玉屏县", "江口县", "石阡县", "思南县",
                "德江县", "沿河县", "印江县", "其他"
            ]
        current_county = county_list[2] if len(county_list) > 2 else county_list[0] if county_list else ""

        w1 = get_field_width(page, ratio=2, subtract=60)
        w2 = get_field_width(page, ratio=1, subtract=40)
        w3 = get_field_width(page, ratio=3, subtract=80)

        cust_input = ft.TextField(label="客户名称", hint_text="输入2字以上查询", width=w1)
        cust_suggestions = ft.Column(spacing=0, visible=False)

        def load_customer_suggestions(val):
            if len(val) < 2:
                cust_suggestions.controls.clear()
                cust_suggestions.visible = False
                cust_suggestions.update()
                page.update()
                return
            conn = get_db_conn()
            if not conn:
                return
            cur = conn.cursor()
            cur.execute(
                "SELECT name, phone, card_holder, card_no, county, street, community, detail_addr FROM base_customer WHERE name LIKE %s LIMIT 8",
                (f"%{val}%",))
            rows = cur.fetchall()
            conn.close()
            cust_suggestions.controls.clear()
            if not rows:
                cust_suggestions.visible = False
                cust_suggestions.update()
                page.update()
                return
            for row in rows:
                card = ft.Card(
                    content=ft.Container(
                        content=ft.Text(f"{row[0]} | {row[1]}"),
                        padding=10,
                        on_click=lambda e, r=row: select_customer(r)
                    )
                )
                cust_suggestions.controls.append(card)
            cust_suggestions.visible = True
            cust_suggestions.update()
            page.update()

        def select_customer(row):
            nonlocal current_county
            cust_input.value = row[0]
            phone.value = row[1] or ""
            card_holder.value = row[2] or ""
            card_no.value = row[3] or ""
            if row[4]:
                selected_county_text.value = row[4]
                current_county = row[4]
                load_streets()
            street_dropdown.value = row[5] or None
            community_input.value = row[6] or ""
            detail_addr.value = row[7] or ""
            cust_suggestions.controls.clear()
            cust_suggestions.visible = False
            cust_suggestions.update()
            page.update()

        model_input_width = w2
        scan_btn = ft.IconButton(
            ft.Icons.CAMERA_ALT,
            icon_size=24,
            tooltip="扫码识别型号",
            on_click=lambda e: scan_barcode_from_image(page, on_scan_success),
            style=ft.ButtonStyle(bgcolor=ft.Colors.TRANSPARENT),
            opacity=0.6,
        )
        model_input = ft.TextField(
            label="商品型号",
            hint_text="输入2字以上查询",
            width=model_input_width,
            suffix=scan_btn,
        )
        model_suggestions = ft.Column(spacing=0, visible=False)

        def load_model_suggestions(val):
            if len(val) < 2:
                model_suggestions.controls.clear()
                model_suggestions.visible = False
                model_suggestions.update()
                page.update()
                return
            conn = get_db_conn()
            if not conn:
                return
            cur = conn.cursor()
            cur.execute(
                "SELECT model, price, union_subsidy, gov_subsidy, old_discount FROM base_product WHERE model LIKE %s LIMIT 8",
                (f"%{val}%",))
            rows = cur.fetchall()
            conn.close()
            model_suggestions.controls.clear()
            if not rows:
                model_suggestions.visible = False
                model_suggestions.update()
                page.update()
                return
            for row in rows:
                card = ft.Card(
                    content=ft.Container(
                        content=ft.Text(f"{row[0]} (¥{row[1]})"),
                        padding=10,
                        on_click=lambda e, r=row: select_product(r)
                    )
                )
                model_suggestions.controls.append(card)
            model_suggestions.visible = True
            model_suggestions.update()
            page.update()

        def select_product(row):
            model_input.value = row[0]
            price.value = str(row[1] or 0)
            union_subsidy.value = str(row[2] or 0)
            gov_subsidy.value = str(row[3] or 0)
            old_discount.value = str(row[4] or 0)
            model_suggestions.controls.clear()
            model_suggestions.visible = False
            model_suggestions.update()
            page.update()

        cust_input.on_change = lambda e: load_customer_suggestions(cust_input.value.strip())
        model_input.on_change = lambda e: load_model_suggestions(model_input.value.strip())

        phone = ft.TextField(label="联系电话", width=w1)
        card_holder = ft.TextField(label="工会卡持卡人", width=w1)
        card_no = ft.TextField(label="工会卡号", width=w1)

        default_county = county_list[2] if len(county_list) > 2 else (county_list[0] if county_list else "")
        selected_county_text = ft.Text(default_county)
        county_selector = ft.Stack(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            selected_county_text,
                            ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=ft.Colors.OUTLINE)
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER
                    ),
                    width=w1,
                    padding=ft.Padding(left=10, top=16, right=10, bottom=10),
                    border=ft.Border(
                        left=ft.BorderSide(width=1, color=ft.Colors.OUTLINE),
                        right=ft.BorderSide(width=1, color=ft.Colors.OUTLINE),
                        top=ft.BorderSide(width=1, color=ft.Colors.OUTLINE),
                        bottom=ft.BorderSide(width=1, color=ft.Colors.OUTLINE)
                    ),
                    border_radius=4,
                    bgcolor=ft.Colors.WHITE
                ),
                ft.Container(
                    content=ft.Text("所在县", size=12, color=ft.Colors.OUTLINE),
                    left=8,
                    top=-7,
                    bgcolor=ft.Colors.WHITE,
                    padding=ft.Padding(left=2, right=2, top=0, bottom=0)
                )
            ],
            width=w1
        )

        street_dropdown = ft.Dropdown(label="街道", width=w1, options=[])
        community_input = ft.TextField(label="小区/村", width=w1)
        detail_addr = ft.TextField(label="详细地址", width=w1)

        def load_streets():
            nonlocal current_county
            if not current_county:
                street_dropdown.options.clear()
                street_dropdown.value = None
                street_dropdown.update()
                page.update()
                return
            street_list = []
            conn = get_db_conn()
            if conn:
                try:
                    cur = conn.cursor()
                    sql = "SELECT street FROM base_address WHERE TRIM(county) = %s GROUP BY street ORDER BY MIN(id)"
                    cur.execute(sql, (current_county,))
                    street_list = [row[0].strip() for row in cur.fetchall()]
                except Exception as ex:
                    print(f"加载街道失败: {ex}")
                finally:
                    conn.close()
            street_dropdown.options = [ft.dropdown.Option(s) for s in street_list]
            street_dropdown.value = None
            street_dropdown.update()
            page.update()

        def build_county_handler(county_name):
            def handler(e):
                nonlocal current_county
                current_county = county_name
                selected_county_text.value = county_name
                county_selector.update()
                load_streets()
            return handler

        county_menu_items = [
            ft.PopupMenuItem(
                content=ft.Text(c),
                on_click=build_county_handler(c)
            )
            for c in county_list
        ]
        county_popup = ft.PopupMenuButton(content=county_selector, items=county_menu_items)

        send_date = ft.TextField(label="拟送货日期", hint_text="YYYY-MM-DD", value=date.today().isoformat(), width=w1)
        order_remark = ft.TextField(label="订单备注", width=w1)

        out_order_no = ft.TextField(label="外部订单号", value="000000", width=w3)
        qty = ft.TextField(label="数量", value="1", width=w3)
        price = ft.TextField(label="单价", width=w3)
        old_discount = ft.TextField(label="旧机折扣(元)", value="0", width=w3)
        union_subsidy = ft.TextField(label="工会补贴%", value="0", width=w3)
        gov_subsidy = ft.TextField(label="国家补贴%", value="0", width=w3)
        store_discount = ft.TextField(label="门店优惠(元)", value="0", width=w3)
        item_remark = ft.TextField(label="商品备注", width=w3)
        need_install_cb = ft.Checkbox(label="需要安装", value=False)

        add_btn = ft.Button("添加商品", icon=ft.Icons.ADD)

        items_list = ft.Column(spacing=5)
        total_label = ft.Text("合计: 0.00 元", size=16, weight=ft.FontWeight.BOLD)
        items = []

        def on_scan_success(code, prod):
            if prod:
                model_input.value = prod["model"]
                price.value = str(prod["price"])
                union_subsidy.value = str(prod.get("union_subsidy", 0))
                gov_subsidy.value = str(prod.get("gov_subsidy", 0))
                old_discount.value = str(prod.get("old_discount", 0))
                page.update()
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"已加载产品: {prod['model']}"),
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.Margin(20, 0, 20, 80),
                    duration=1500,
                )
                page.snack_bar.open = True
                page.update()
            else:
                def after_add(m):
                    model_input.value = m
                    page.update()
                add_product_from_scan(page, code, after_add)

        def refresh_items():
            items_list.controls.clear()
            total = 0.0
            for idx, it in enumerate(items):
                total += it["total"]
                items_list.controls.append(
                    ft.Row([
                        ft.Text(
                            f"{it['model']} x{it['qty']}  ¥{it['total']:.2f}  {'[安装]' if it['need_install'] else ''}"),
                        ft.IconButton(ft.Icons.DELETE, on_click=lambda e, i=idx: remove_item(i))
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                )
            total_label.value = f"合计: {total:.2f} 元"
            page.update()

        def remove_item(idx):
            items.pop(idx)
            refresh_items()

        def add_item(e):
            m = model_input.value.strip()
            try:
                qt = int(qty.value or 0)
                unit_price = float(price.value or 0)
                old = float(old_discount.value or 0)
                union = float(union_subsidy.value or 0)
                gov = float(gov_subsidy.value or 0)
                store = float(store_discount.value or 0)
            except:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text("数量和金额必须是数字"),
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.Margin(20, 0, 20, 80),
                    duration=1500,
                )
                page.snack_bar.open = True
                page.update()
                return
            if not m or qt <= 0 or unit_price <= 0:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text("请完整填写商品信息（型号、数量>0、单价>0）"),
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.Margin(20, 0, 20, 80),
                    duration=1500,
                )
                page.snack_bar.open = True
                page.update()
                return
            prod = get_product_by_model(m)
            if not prod:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"型号 {m} 不存在，请先添加产品"),
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.Margin(20, 0, 20, 80),
                    duration=1500,
                )
                page.snack_bar.open = True
                page.update()

                def after_add(new_model):
                    model_input.value = new_model
                    page.update()
                add_product_from_scan(page, "", after_add)
                return

            after_old = unit_price - old
            after_union = after_old * (1 - union / 100)
            after_store = after_union - store
            if gov == 0:
                final_unit = after_store
            else:
                if after_store <= 10000:
                    final_unit = after_store * (1 - gov / 100)
                else:
                    final_unit = after_store - 1500
            total = final_unit * qt
            total = round(total, 2)
            t_price = round(after_store, 2)

            items.append({
                "model": m,
                "out_order_no": out_order_no.value.strip(),
                "qty": qt,
                "price": unit_price,
                "old_discount": old,
                "union_subsidy": union,
                "gov_subsidy": gov,
                "store_discount": store,
                "t_price": t_price,
                "total": total,
                "need_install": need_install_cb.value,
                "sale_remark": item_remark.value,
                "factory": prod["factory"],
                "category": prod["category"],
                "spec": prod["spec"],
                "piece": prod["piece"],
                "code": prod["code"]
            })
            refresh_items()
            model_input.value = ""
            out_order_no.value = ""
            qty.value = "1"
            price.value = ""
            old_discount.value = "0"
            union_subsidy.value = "0"
            gov_subsidy.value = "0"
            store_discount.value = "0"
            item_remark.value = ""
            need_install_cb.value = False
            page.update()

        add_btn.on_click = add_item

        def save_order(e):
            print("=== 保存订单按钮被点击 ===")
            if not cust_input.value:
                show_alert(page,"提示", "客户名称不能为空")
                return
            if not items:
                show_alert(page,"提示", "请至少添加一个商品")
                return
            county = current_county
            street = street_dropdown.value
            community = community_input.value
            receiver_phone = f"{cust_input.value} {phone.value}"
            if not county:
                show_alert(page,"提示", "请选择所在县")
                return
            full_addr = f"{county}{street or ''}{community or ''}{detail_addr.value or ''}"
            try:
                send_dt = datetime.strptime(send_date.value, "%Y-%m-%d").date()
            except:
                show_alert(page,"错误", "送货日期格式错误，应为YYYY-MM-DD")
                return
            conn = get_db_conn()
            if not conn:
                show_alert(page,"错误", "数据库连接失败")
                return
            cur = conn.cursor()
            try:
                cur.execute("""INSERT INTO sale_main 
                            (order_no, order_date, send_date, cust_name, phone, receiver_phone, card_holder, card_no, county, street, community, detail_addr, full_addr, remark, order_type, sales_name)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (order_no, date.today(), send_dt, cust_input.value, phone.value, receiver_phone,
                             card_holder.value, card_no.value, county, street, community, detail_addr.value, full_addr,
                             order_remark.value, "标准销售", current_user["real_name"]))

                for it in items:
                    cur.execute("""INSERT INTO sale_items 
                                (order_no, out_order_no, model, qty, price, old_discount, union_subsidy, gov_subsidy, store_discount,
                                 t_price, total, need_install, sale_remark, factory, category, spec, piece)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (order_no, it["out_order_no"], it["model"], it["qty"], it["price"], it["old_discount"],
                                 it["union_subsidy"] / 100, it["gov_subsidy"] / 100, it["store_discount"],
                                 it["t_price"], it["total"], 1 if it["need_install"] else 0, it["sale_remark"],
                                 it["factory"], it["category"], it["spec"], it["piece"]))
                    cur.execute("SELECT qty FROM stock_now WHERE model=%s", (it["model"],))
                    stock = cur.fetchone()
                    if stock:
                        cur.execute("UPDATE stock_now SET qty = qty - %s, s_qty = s_qty - %s WHERE model=%s",
                                    (it["qty"], it["qty"], it["model"]))
                    else:
                        cur.execute(
                            "INSERT INTO stock_now (factory, model, spec, qty, s_qty) VALUES (%s, %s, %s, %s, %s)",
                            (it["factory"], it["model"], it["spec"], -it["qty"], -it["qty"])
                        )
                    cur.execute("""INSERT INTO transport 
                                (order_date, order_no, out_order_no, cust_name, phone, full_addr, factory, category, model, spec, t_qty, send_date, status)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (date.today(), order_no, it["out_order_no"], cust_input.value, phone.value, full_addr,
                                 it["factory"], it["category"], it["model"], it["spec"], it["qty"], send_dt, "待派单"))
                    if it["need_install"]:
                        cur.execute("""INSERT INTO install 
                                    (order_date, order_no, cust_name, phone, factory, model, spec, i_qty, status)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                    (date.today(), order_no, cust_input.value, phone.value,
                                     it["factory"], it["model"], it["spec"], it["qty"], "待安装"))

                cur.execute("SELECT total_amount FROM base_customer WHERE name=%s AND phone=%s",
                            (cust_input.value, phone.value))
                cust = cur.fetchone()
                total_order = sum(it["total"] for it in items)
                if cust:
                    cur.execute("UPDATE base_customer SET total_amount = total_amount + %s WHERE name=%s AND phone=%s",
                                (total_order, cust_input.value, phone.value))
                else:
                    cur.execute("SELECT MAX(cust_id) FROM base_customer")
                    max_id = cur.fetchone()[0]
                    num = int(max_id[1:]) + 1 if max_id else 1
                    cust_id = f"C{num:05d}"
                    cur.execute("""INSERT INTO base_customer 
                                (cust_id, name, phone, card_holder, card_no, county, street, community, detail_addr, full_addr, total_amount, level)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (cust_id, cust_input.value, phone.value, card_holder.value, card_no.value,
                                 county, street, community, detail_addr.value, full_addr, total_order, "三级"))

                conn.commit()

                def on_success(e):
                    nonlocal current_county
                    cust_input.value = ""
                    phone.value = ""
                    card_holder.value = ""
                    card_no.value = ""
                    if county_list:
                        current_county = county_list[0] if county_list else ""
                        selected_county_text.value = current_county
                    street_dropdown.options.clear()
                    street_dropdown.value = None
                    community_input.value = ""
                    detail_addr.value = ""
                    order_remark.value = ""
                    send_date.value = date.today().isoformat()
                    items.clear()
                    refresh_items()
                    page.update()

                show_alert(page,"成功", f"订单 {order_no} 保存成功", on_success)

            except Exception as ex:
                conn.rollback()
                print(f"保存异常: {ex}")
                show_alert(page,"错误", f"保存失败: {ex}")
            finally:
                conn.close()

        save_btn = ft.Button("💾 保存订单", icon=ft.Icons.SAVE, on_click=save_order, bgcolor=ft.Colors.GREEN,
                             color=ft.Colors.WHITE)
        query_btn = ft.Button("🔍 查询订单", icon=ft.Icons.SEARCH, on_click=lambda e: show_order_query(),
                              bgcolor=ft.Colors.BLUE_500, color=ft.Colors.WHITE)
        btn_row = ft.Row(
            [save_btn, query_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        )

        cust_container = ft.Column(
            [
                cust_input,
                cust_suggestions
            ],
            spacing=0
        )
        model_container = ft.Column(
            [
                model_input,
                model_suggestions
            ],
            spacing=0,
            width=model_input_width,
        )

        main_content.controls.append(
            ft.Column(
                [
                    ft.Text("新建销售订单", size=20, weight=ft.FontWeight.BOLD),
                    ft.Row([cust_container, phone], spacing=10, wrap=True),
                    ft.Row([card_holder, card_no], spacing=10, wrap=True),
                    ft.Row([county_popup, street_dropdown], spacing=10, wrap=True),
                    ft.Row([community_input, detail_addr], spacing=10, wrap=True),
                    ft.Row([send_date, order_remark], spacing=10, wrap=True),
                    ft.Text("商品信息", weight=ft.FontWeight.BOLD),
                    ft.Row([model_container], alignment=ft.MainAxisAlignment.START),
                    ft.Row([out_order_no, qty, price], alignment=ft.MainAxisAlignment.START, wrap=True),
                    ft.Row([old_discount, union_subsidy, gov_subsidy], alignment=ft.MainAxisAlignment.START, wrap=True),
                    ft.Row([store_discount, item_remark, need_install_cb], alignment=ft.MainAxisAlignment.START,
                           wrap=True),
                    add_btn,
                    ft.Text("商品清单", weight=ft.FontWeight.BOLD),
                    items_list,
                    total_label,
                    btn_row,
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO
            )
        )
        page.update()

        if county_list:
            current_county = county_list[2] if len(county_list) > 2 else county_list[0] if county_list else ""
            selected_county_text.value = current_county
            load_streets()

    # ---------------------------- 订单查询（适配屏幕） ----------------------------
    def show_order_query():
        main_content.controls.clear()

        total_spacing = 10
        field_width = get_field_width(page,ratio=2, subtract=60)
        btn_width = field_width

        order_no_input = ft.TextField(label="订单号", width=field_width)
        out_order_no_input = ft.TextField(label="外部订单号", width=field_width)
        cust_name_input = ft.TextField(label="客户姓名", width=field_width)
        phone_input = ft.TextField(label="联系方式", width=field_width)
        address_input = ft.TextField(label="地址", width=field_width)
        brand_input = ft.TextField(label="品牌", width=field_width)
        category_input = ft.TextField(label="品类", width=field_width)
        model_input = ft.TextField(label="型号", width=field_width)

        selected_date_str = None
        date_display = ft.Text("选择日期", size=14, color=ft.Colors.GREY_700)

        def on_date_picked(e):
            nonlocal selected_date_str
            if date_picker.value:
                dt = date_picker.value + timedelta(days=1)
                selected_date_str = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
                date_display.value = selected_date_str
                date_display.color = ft.Colors.BLACK
            else:
                selected_date_str = None
                date_display.value = "选择日期"
                date_display.color = ft.Colors.GREY_700
            date_display.update()
            date_picker.open = False
            page.update()

        date_picker = ft.DatePicker(
            on_change=on_date_picked,
            first_date=datetime(2020, 1, 1),
            last_date=datetime(2030, 12, 31),
        )
        page.overlay.append(date_picker)

        def pick_date(e):
            date_picker.open = True
            page.update()

        date_picker_btn = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CALENDAR_TODAY, size=20, color=ft.Colors.BLUE),
                    date_display,
                ],
                alignment=ft.MainAxisAlignment.START,
                spacing=5,
            ),
            padding=ft.Padding(left=10, top=8, right=10, bottom=8),
            border=ft.Border.all(1, ft.Colors.OUTLINE),
            border_radius=8,
            width=field_width,
            on_click=pick_date,
            ink=True,
        )

        result_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)

        def load_orders(is_default=False):
            result_list.controls.clear()
            order_no = order_no_input.value.strip() if order_no_input.value else None
            out_order_no = out_order_no_input.value.strip() if out_order_no_input.value else None
            cust_name = cust_name_input.value.strip() if cust_name_input.value else None
            phone = phone_input.value.strip() if phone_input.value else None
            address = address_input.value.strip() if address_input.value else None
            brand = brand_input.value.strip() if brand_input.value else None
            category = category_input.value.strip() if category_input.value else None
            model = model_input.value.strip() if model_input.value else None

            if is_default:
                date_val = datetime.now().strftime("%Y-%m-%d")
            else:
                date_val = selected_date_str

            conn = get_db_conn()
            if not conn:
                result_list.controls.append(ft.Text("数据库连接失败"))
                page.update()
                return
            cur = conn.cursor()
            sql = """
                SELECT DISTINCT 
                    m.order_no, m.order_date, m.cust_name, m.phone, m.full_addr,
                    GROUP_CONCAT(DISTINCT i.model SEPARATOR ', ') AS models,
                    IFNULL(SUM(i.total), 0) AS total_amount
                FROM sale_main m
                JOIN sale_items i ON m.order_no = i.order_no
                WHERE 1=1
            """
            params = []
            if order_no:
                sql += " AND m.order_no LIKE %s"
                params.append(f"%{order_no}%")
            if out_order_no:
                sql += " AND i.out_order_no LIKE %s"
                params.append(f"%{out_order_no}%")
            if cust_name:
                sql += " AND m.cust_name LIKE %s"
                params.append(f"%{cust_name}%")
            if phone:
                sql += " AND m.phone LIKE %s"
                params.append(f"%{phone}%")
            if address:
                sql += " AND m.full_addr LIKE %s"
                params.append(f"%{address}%")
            if brand:
                sql += " AND i.factory LIKE %s"
                params.append(f"%{brand}%")
            if category:
                sql += " AND i.category LIKE %s"
                params.append(f"%{category}%")
            if model:
                sql += " AND i.model LIKE %s"
                params.append(f"%{model}%")
            if date_val:
                sql += " AND DATE(m.order_date) = %s"
                params.append(date_val)
            sql += " GROUP BY m.order_no, m.order_date, m.cust_name, m.phone, m.full_addr ORDER BY m.order_date DESC"

            try:
                cur.execute(sql, params)
                rows = cur.fetchall()
                conn.close()
            except Exception as ex:
                conn.close()
                result_list.controls.append(ft.Text(f"查询失败: {ex}"))
                page.update()
                return

            if not rows:
                result_list.controls.append(ft.Text("未找到订单，请调整查询条件", size=16))
                page.update()
                return

            for row in rows:
                order_no, order_date, cust_name, phone, full_addr, models, total = row
                total = float(total) if total else 0.0
                card = ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(f"订单号: {order_no}", weight=ft.FontWeight.BOLD),
                                ft.Text(f"日期: {order_date}  客户: {cust_name}  电话: {phone}"),
                                ft.Text(f"地址: {full_addr}"),
                                ft.Text(f"商品: {models or '无商品'}"),
                                ft.Text(f"总金额: ¥{total:.2f}", color=ft.Colors.GREEN, weight=ft.FontWeight.BOLD),
                            ],
                            spacing=5,
                        ),
                        padding=15,
                        on_click=lambda e, o=order_no: show_order_detail(o),
                    ),
                    elevation=2,
                )
                result_list.controls.append(card)
            page.update()

        def reset_search():
            nonlocal selected_date_str
            order_no_input.value = ""
            out_order_no_input.value = ""
            cust_name_input.value = ""
            phone_input.value = ""
            address_input.value = ""
            brand_input.value = ""
            category_input.value = ""
            model_input.value = ""
            selected_date_str = None
            date_display.value = "选择日期"
            date_display.color = ft.Colors.GREY_700
            date_display.update()
            load_orders(is_default=True)

        # ---------- 订单详情（适配屏幕） ----------
        def show_order_detail(order_no):
            detail_win = ft.AlertDialog(
                title=ft.Text(f"订单详情 - {order_no}"),
                content=ft.Column(
                    [
                        ft.Text("商品明细:", weight=ft.FontWeight.BOLD),
                        ft.Column(spacing=5),
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                    width=min(page.window_width * 0.9 if page.window_width else 400, 500),
                    height=min(page.window_height * 0.7 if page.window_height else 500, 600),
                ),
                actions=[
                    ft.TextButton("关闭", on_click=lambda e: setattr(detail_win, 'open', False))
                ],
            )
            page.overlay.append(detail_win)
            detail_win.open = True
            page.update()

            def load_items():
                container = detail_win.content.controls[1]
                container.controls.clear()
                conn = get_db_conn()
                if not conn:
                    container.controls.append(ft.Text("数据库连接失败"))
                    page.update()
                    return
                cur = conn.cursor()
                cur.execute("""
                    SELECT out_order_no, model, qty, total, full_out_no, id 
                    FROM sale_items 
                    WHERE order_no = %s
                """, (order_no,))
                rows = cur.fetchall()
                conn.close()
                if not rows:
                    container.controls.append(ft.Text("无商品明细"))
                    page.update()
                    return
                for row in rows:
                    out_no, model, qty, total, full_out_no, item_id = row
                    total = float(total) if total else 0.0
                    item_card = ft.Card(
                        content=ft.Container(
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(f"{model} x {qty}", weight=ft.FontWeight.BOLD),
                                            ft.Text(f"¥{total:.2f}", color=ft.Colors.GREEN),
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    ft.Text(f"外部单号: {out_no}", size=12),
                                    ft.Text(f"完整外部单号: {full_out_no or '未录入'}", size=12,
                                            color=ft.Colors.BLUE if full_out_no else ft.Colors.GREY),
                                    ft.Row(
                                        [
                                            ft.IconButton(
                                                ft.Icons.CAMERA_ALT,
                                                icon_size=20,
                                                tooltip="拍摄支付凭证并识别二维码",
                                                on_click=lambda e, o=order_no, out=out_no, item=item_id:
                                                capture_payment_voucher(o, out, item)
                                            ),
                                            ft.Text("拍摄凭证", size=12),
                                        ],
                                        alignment=ft.MainAxisAlignment.END,
                                    ),
                                ],
                                spacing=5,
                            ),
                            padding=10,
                        ),
                        elevation=1,
                    )
                    container.controls.append(item_card)
                page.update()

            # 拍摄凭证 - 使用扫码函数
            def capture_payment_voucher(order_no, out_order_no, item_id):
                def on_scan_code(code):
                    # 识别到二维码内容，更新数据库
                    conn = get_db_conn()
                    cur = conn.cursor()
                    cur.execute("UPDATE sale_items SET full_out_no = %s WHERE id = %s", (code, item_id))
                    conn.commit()
                    conn.close()
                    load_items()
                    page.snack_bar = ft.SnackBar(ft.Text(f"凭证识别成功，完整外部单号: {code}"), duration=3000)
                    page.snack_bar.open = True
                    page.update()

                scan_barcode_only(page, on_scan_code, title="识别支付凭证二维码")

            load_items()

        def on_query_click(e):
            load_orders(is_default=False)

        action_row = ft.Row(
            [
                date_picker_btn,
                ft.Button("查询", icon=ft.Icons.SEARCH, on_click=on_query_click, width=btn_width),
                ft.Button("重置", on_click=lambda e: reset_search(), width=btn_width),
            ],
            spacing=10,
        )

        query_panel = ft.Column(
            [
                ft.Text("订单查询", size=20, weight=ft.FontWeight.BOLD),
                ft.Row([order_no_input, out_order_no_input], spacing=10),
                ft.Row([cust_name_input, phone_input], spacing=10),
                ft.Row([address_input, brand_input], spacing=10),
                ft.Row([category_input, model_input], spacing=10),
                action_row,
                ft.Divider(height=10),
                result_list,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )
        main_content.controls.append(query_panel)
        load_orders(is_default=True)
        page.update()

    # ---------------------------- 入库管理 ----------------------------
    def show_inbound():
        nonlocal current_user
        main_content.controls.clear()

        input_height = 50
        input_width = get_field_width(page,ratio=1, subtract=40)

        title = ft.Text("商品入库", size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.LEFT)

        inbound_type = ft.Container(
            content=ft.Dropdown(
                label="入库类型",
                options=[
                    ft.dropdown.Option("标准入库"),
                    ft.dropdown.Option("退货入库"),
                    ft.dropdown.Option("调拨入库")
                ],
                value="标准入库",
                width=input_width,
            ),
            height=input_height,
            width=input_width,
        )

        scan_btn = ft.IconButton(
            ft.Icons.CAMERA_ALT,
            icon_size=24,
            tooltip="扫码识别型号",
            on_click=lambda e: scan_barcode_from_image(page, on_scan),
            style=ft.ButtonStyle(bgcolor=ft.Colors.TRANSPARENT),
            opacity=0.6,
        )
        model_input = ft.TextField(
            label="商品型号",
            hint_text="输入2字以上自动查询",
            width=input_width,
            height=input_height,
            suffix=scan_btn,
        )

        model_suggestions = ft.Column(spacing=0, visible=False)

        def load_model_suggestions(val):
            if len(val) < 2:
                model_suggestions.controls.clear()
                model_suggestions.visible = False
                model_suggestions.update()
                page.update()
                return
            conn = get_db_conn()
            if not conn:
                return
            cur = conn.cursor()
            cur.execute("SELECT model, factory, spec FROM base_product WHERE model LIKE %s LIMIT 10", (f"%{val}%",))
            rows = cur.fetchall()
            conn.close()
            model_suggestions.controls.clear()
            if not rows:
                model_suggestions.visible = False
                model_suggestions.update()
                page.update()
                return
            for row in rows:
                card = ft.Card(
                    content=ft.Container(
                        content=ft.Text(f"{row[0]} | {row[1]} | {row[2]}", size=13),
                        padding=12,
                        on_click=lambda e, r=row: select_product(r)
                    ),
                    elevation=0,
                    margin=ft.Margin(0, 0, 0, 2),
                )
                model_suggestions.controls.append(card)
            model_suggestions.visible = True
            model_suggestions.update()
            page.update()

        def select_product(row):
            model_input.value = row[0]
            model_suggestions.controls.clear()
            model_suggestions.visible = False
            model_suggestions.update()
            page.update()

        model_input.on_change = lambda e: load_model_suggestions(e.control.value.strip())

        model_column = ft.Column(
            [
                model_input,
                model_suggestions,
            ],
            spacing=0,
            width=input_width,
        )

        def on_scan(code, prod):
            if prod:
                model_input.value = prod["model"]
                model_suggestions.controls.clear()
                model_suggestions.visible = False
                model_suggestions.update()
                page.update()
            else:
                def after_add(m):
                    model_input.value = m
                    model_suggestions.controls.clear()
                    model_suggestions.visible = False
                    model_suggestions.update()
                    page.update()
                add_product_from_scan(page, code, after_add)

        qty = ft.TextField(label="入库数量", width=input_width, height=input_height)
        in_price = ft.TextField(label="入库价格", value="0", width=input_width, height=input_height)
        location = ft.TextField(label="库位", width=input_width, height=input_height)
        in_date = ft.TextField(label="入库日期", value=date.today().isoformat(), width=input_width, height=input_height)

        def save_inbound(e):
            print("=== 确认入库按钮被点击 ===")

            model_suggestions.controls.clear()
            model_suggestions.visible = False
            model_suggestions.update()
            page.update()

            if not isinstance(current_user, dict):
                show_alert(page,"错误", "用户信息异常，请重新登录")
                return

            m = model_input.value.strip()
            if not m:
                show_alert(page,"提示", "请输入商品型号")
                return

            try:
                qt = int(qty.value) if qty.value else 0
                if qt <= 0:
                    raise ValueError
            except ValueError:
                show_alert(page,"错误", "请输入有效的正整数")
                return

            try:
                price = float(in_price.value) if in_price.value else 0.0
                if price < 0:
                    raise ValueError
            except ValueError:
                show_alert(page,"错误", "请输入有效的数字（入库价格）")
                return

            conn = get_db_conn()
            if not conn:
                show_alert(page,"错误", "数据库连接失败，请检查配置")
                return

            prod = get_product_by_model(m)
            if not prod:
                conn.close()
                show_alert(page,"提示", f"型号 {m} 不存在，请先添加产品")
                return

            cur = conn.cursor()
            try:
                operator = current_user.get("real_name", "未知用户")
                cur.execute("""INSERT INTO stock_in 
                            (inbound_type, factory, category, model, code, spec, qty, in_price,
                             union_subsidy, gov_subsidy, old_discount, location, in_date, operator)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (inbound_type.content.value, prod["factory"], prod["category"], m, prod["code"],
                             prod["spec"], qt, price, prod["union_subsidy"], prod["gov_subsidy"], prod["old_discount"],
                             location.value, in_date.value, operator))
                cur.execute("""INSERT INTO stock_now (factory, model, spec, qty, s_qty)
                            VALUES (%s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE qty = qty + %s, s_qty = s_qty + %s""",
                            (prod["factory"], m, prod["spec"], qt, qt, qt, qt))
                conn.commit()
                print(f"入库成功：{m} × {qt}，单价：{price}")

                def on_success(e):
                    model_input.value = ""
                    qty.value = ""
                    in_price.value = "0"
                    location.value = ""
                    in_date.value = date.today().isoformat()
                    model_suggestions.controls.clear()
                    model_suggestions.visible = False
                    model_suggestions.update()
                    page.update()

                show_alert(page,"成功", f"入库 {qt} 件成功", on_success)

            except Exception as ex:
                conn.rollback()
                print("入库异常:", ex)
                show_alert(page,"错误", f"入库失败: {ex}")
            finally:
                conn.close()

        save_btn = ft.Button(
            "确认入库",
            icon=ft.Icons.SAVE,
            on_click=save_inbound,
            bgcolor=ft.Colors.GREEN,
            color=ft.Colors.WHITE,
            width=input_width,
            height=input_height,
        )

        main_content.controls.append(
            ft.Column(
                [
                    title,
                    inbound_type,
                    model_column,
                    qty,
                    in_price,
                    location,
                    in_date,
                    save_btn,
                ],
                spacing=15,
                scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
        page.update()

    # ---------------------------- 运输管理（含SN码多码选择和失败处理） ----------------------------
    def show_transport():
        main_content.controls.clear()

        w1 = get_field_width(page,ratio=2, subtract=60)
        w2 = get_field_width(page,ratio=3, subtract=80)

        status_dropdown = ft.Dropdown(
            label="订单状态",
            options=[
                ft.dropdown.Option("全部"),
                ft.dropdown.Option("待派单"),
                ft.dropdown.Option("待出库"),
                ft.dropdown.Option("已出库"),
                ft.dropdown.Option("待自提"),
                ft.dropdown.Option("已自提"),
                ft.dropdown.Option("已送货入户"),
            ],
            value="待出库",
            width=w1,
        )
        start_date = ft.TextField(label="起始日期", hint_text="YYYY-MM-DD", width=w2)
        end_date = ft.TextField(label="结束日期", hint_text="YYYY-MM-DD", width=w2)
        order_no_input = ft.TextField(label="订单号", width=w2)
        cust_name_input = ft.TextField(label="客户名称", width=w2)
        query_btn = ft.Button("查询", icon=ft.Icons.SEARCH)
        reset_btn = ft.Button("重置", icon=ft.Icons.REFRESH)

        trans_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)

        def get_home_photo_biz_info(order_no, out_order_no):
            try:
                out_int = int(out_order_no) if out_order_no else 0
            except (ValueError, TypeError):
                out_int = 0

            if out_int <= 20:
                biz_no = f"{order_no}_{out_int}"
                prefix = "ORD"
            else:
                biz_no = str(out_order_no)
                prefix = "HM"
            return biz_no, prefix

        def load_trans():
            trans_list.controls.clear()
            conn = get_db_conn()
            if not conn:
                show_alert(page,"错误", "数据库连接失败")
                return

            status = status_dropdown.value
            s_date = start_date.value.strip()
            e_date = end_date.value.strip()
            order_no = order_no_input.value.strip()
            cust_name = cust_name_input.value.strip()

            if status in ["已送货入户", "已自提"]:
                date_field = "trans_date"
            else:
                date_field = "order_date"

            sql = f"""
                SELECT id, order_date, order_no, out_order_no, cust_name, phone, full_addr,
                       factory, category, model, t_qty, trans_remark,
                       status, send_date, trans_date,
                       COALESCE(delivery01_name,''), COALESCE(delivery02_name,''),
                       sn_code, sn_photo, home_photo
                FROM transport
                WHERE 1=1
            """
            params = []
            if status and status != "全部":
                sql += " AND status = %s"
                params.append(status)
            if s_date and e_date:
                sql += f" AND {date_field} BETWEEN %s AND %s"
                params.extend([s_date, e_date])
            if order_no:
                sql += " AND order_no LIKE %s"
                params.append(f"%{order_no}%")
            if cust_name:
                sql += " AND cust_name LIKE %s"
                params.append(f"%{cust_name}%")

            sql += f" ORDER BY {date_field} DESC"
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()

            if not rows:
                trans_list.controls.append(ft.Text("暂无符合条件的运输任务", size=16))
                page.update()
                return

            for row in rows:
                trans_id, order_date, order_no, out_order_no, cust_name, phone, full_addr, factory, category, model, t_qty, trans_remark, status_val, send_date_val, trans_date_val, delivery01_name, delivery02_name, sn_code, sn_photo, home_photo = row
                tag = "normal"
                today = date.today()
                try:
                    if send_date_val and isinstance(send_date_val, str):
                        send_dt = datetime.strptime(send_date_val, "%Y-%m-%d").date()
                    else:
                        send_dt = send_date_val
                    if isinstance(send_dt, date) and send_dt < today:
                        if status_val == "待派单":
                            tag = "overdue"
                        elif status_val == "待出库":
                            tag = "orange"
                    if status_val in ["已出库", "待自提", "已自提", "已送货入户"]:
                        tag = "overtrans"
                except:
                    pass

                border_side = None
                if tag == "overdue":
                    border_side = ft.Border(left=ft.BorderSide(4, ft.Colors.RED))
                elif tag == "orange":
                    border_side = ft.Border(left=ft.BorderSide(4, ft.Colors.ORANGE))
                elif tag == "overtrans":
                    border_side = ft.Border(left=ft.BorderSide(4, ft.Colors.GREEN))

                card = ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(f"订单: {order_no}", weight=ft.FontWeight.BOLD),
                                        ft.Text(f"客户: {cust_name}", weight=ft.FontWeight.BOLD),
                                        ft.Text(f"状态: {status_val}"),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Text(
                                    f"型号: {model}  数量: {t_qty}  计划日: {send_date_val}  实际日: {trans_date_val}"),
                                ft.Text(f"地址: {full_addr}"),
                                ft.Row(
                                    [
                                        ft.IconButton(ft.Icons.EDIT, tooltip="修改状态",
                                                      on_click=lambda e, r=row: change_status(r)),
                                    ]
                                ),
                            ],
                            spacing=5,
                        ),
                        padding=10,
                        on_click=lambda e, r=row: open_operation_dialog(r),
                        bgcolor=ft.Colors.WHITE,
                        border=border_side if border_side else None,
                    ),
                    elevation=2,
                )
                trans_list.controls.append(card)
            page.update()

        def open_operation_dialog(row):
            trans_id, order_date, order_no, out_order_no, cust_name, phone, full_addr, factory, category, model, t_qty, trans_remark, status_val, send_date_val, trans_date_val, delivery01_name, delivery02_name, sn_code, sn_photo, home_photo = row

            current_order = {
                "trans_id": trans_id,
                "order_no": order_no,
                "out_order_no": out_order_no,
                "status": status_val,
                "sn_code": sn_code or "",
                "sn_photo": sn_photo,
                "home_photo": home_photo,
            }

            home_biz_no, home_prefix = get_home_photo_biz_info(order_no, out_order_no)

            sn_entry = ft.TextField(label="SN码", value=current_order["sn_code"], expand=True)
            trans_date_input = ft.TextField(label="实际送货日期", value=date.today().isoformat(), expand=True)
            delivery01 = ft.TextField(label="送  货  人", value=delivery01_name or "麻跃进", expand=True)
            delivery02 = ft.TextField(label="共同送货人", value=delivery02_name or "徐连配", expand=True)
            need_delivery_cb = ft.Checkbox(label="需要送货", value=True)

            status_label = ft.Text(f"当前状态: {status_val}", weight=ft.FontWeight.BOLD)
            sn_photo_status = ft.Text("SN照片: 已上传" if sn_photo else "SN照片: 未上传",
                                      color=ft.Colors.GREEN if sn_photo else ft.Colors.GREY)
            home_photo_status = ft.Text("送货照片: 已上传" if home_photo else "送货照片: 未上传",
                                        color=ft.Colors.GREEN if home_photo else ft.Colors.GREY)

            def do_confirm_out(e):
                if current_order["status"] not in ["待出库", "待派单"]:
                    show_alert(page,"提示", f"当前状态 {current_order['status']}，不能出库")
                    return
                sn_code_input = sn_entry.value.strip()
                trans_date = trans_date_input.value.strip()
                delivery01_name_val = delivery01.value.strip()
                delivery02_name_val = delivery02.value.strip()
                need_delivery = need_delivery_cb.value
                new_status = "已出库" if need_delivery else "待自提"

                conn = get_db_conn()
                cur = conn.cursor()
                try:
                    cur.execute(
                        """UPDATE transport SET status=%s, sn_code=%s, trans_date=%s,
                           delivery01_name=%s, delivery02_name=%s, sn_photo=%s
                           WHERE out_order_no=%s""",
                        (new_status, sn_code_input, trans_date,
                         delivery01_name_val, delivery02_name_val, current_order["sn_photo"],
                         current_order["out_order_no"])
                    )
                    conn.commit()
                    show_alert(page,"成功", f"订单 {current_order['order_no']} → {new_status}")
                    dlg.open = False
                    load_trans()
                except Exception as ex:
                    conn.rollback()
                    show_alert(page,"错误", str(ex))
                finally:
                    conn.close()

            def do_confirm_delivered(e):
                if current_order["status"] not in ["已出库", "待自提"]:
                    show_alert(page,"提示", f"当前状态 {current_order['status']}，不能确认送达")
                    return
                new_status = "已送货入户" if current_order["status"] == "已出库" else "已自提"
                conn = get_db_conn()
                cur = conn.cursor()
                try:
                    cur.execute(
                        "UPDATE transport SET status=%s, trans_date=%s WHERE out_order_no=%s",
                        (new_status, date.today().isoformat(), current_order["out_order_no"])
                    )
                    conn.commit()
                    show_alert(page,"成功", f"订单 {current_order['order_no']} → {new_status}")
                    dlg.open = False
                    load_trans()
                except Exception as ex:
                    conn.rollback()
                    show_alert(page,"错误", str(ex))
                finally:
                    conn.close()

            def do_view_sn_photo(e):
                file_data = get_file_from_db("sn_photos", current_order["out_order_no"])
                if not file_data:
                    show_alert(page,"提示", "该订单暂无 SN 照片")
                    return
                from PIL import Image
                from io import BytesIO
                img = Image.open(BytesIO(file_data))
                img.thumbnail((600, 600))
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                img.save(tmp.name)
                os.startfile(tmp.name)

            def do_view_home_photo(e):
                biz_no, prefix = get_home_photo_biz_info(order_no, out_order_no)
                file_data = get_file_from_db("home_photos", biz_no)
                if not file_data:
                    show_alert(page,"提示", "该订单暂无送货照片")
                    return
                from PIL import Image
                from io import BytesIO
                img = Image.open(BytesIO(file_data))
                img.thumbnail((600, 600))
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                img.save(tmp.name)
                os.startfile(tmp.name)

            # ---------- SN码一体化管理（支持多码选择、失败后手动录入） ----------
            def open_sn_manage_dialog(e):
                sn_dialog = None
                current_mode = "scan"  # scan:扫码模式 / upload:扫码成功后传照片 / manual:手动录入模式

                # 定义视图
                def refresh_view():
                    if current_mode == "scan":
                        sn_dialog.content = build_scan_view()
                    elif current_mode == "upload":
                        sn_dialog.content = build_upload_view()
                    elif current_mode == "manual":
                        sn_dialog.content = build_manual_view()
                    page.update()

                # 视图1：原生扫码（支持多码选择）
                def build_scan_view():
                    tip = ft.Text("对准SN条码自动识别", size=12, text_align=ft.TextAlign.CENTER)
                    # 重试计数
                    scan_attempt = 0

                    def on_scan_success(e):
                        nonlocal scan_attempt
                        # 多码处理
                        barcodes = e.barcodes if hasattr(e, 'barcodes') and e.barcodes else []
                        if len(barcodes) > 1:
                            # 弹窗让用户选择
                            def choose_barcode(code):
                                choice_dlg.open = False
                                page.update()
                                # 处理选中的码
                                process_code(code)

                            items = []
                            for bc in barcodes:
                                code_str = bc.data.decode('utf-8', errors='ignore')
                                items.append(ft.ListTile(
                                    title=ft.Text(code_str),
                                    on_click=lambda _, c=code_str: choose_barcode(c)
                                ))
                            choice_dlg = ft.AlertDialog(
                                title=ft.Text("检测到多个条码，请选择"),
                                content=ft.Column(items, scroll=ft.ScrollMode.AUTO, height=200),
                                actions=[ft.TextButton("取消", on_click=lambda _: (setattr(choice_dlg, 'open', False), page.update()))]
                            )
                            page.overlay.append(choice_dlg)
                            choice_dlg.open = True
                            page.update()
                            return

                        if e.data:
                            process_code(e.data)
                        else:
                            # 识别失败
                            scan_attempt += 1
                            if scan_attempt >= 3:
                                tip.value = "已连续失败3次，请拍照或手动输入"
                                tip.color = ft.Colors.RED
                                # 自动切换到手动模式（但保留拍照入口）
                                nonlocal current_mode
                                current_mode = "manual"
                                refresh_view()
                            else:
                                tip.value = f"未识别到条码，请重试（{scan_attempt}/3）"
                                tip.color = ft.Colors.ORANGE
                            scanner.disabled = False
                            page.update()

                    def process_code(code):
                        sn_code = code.strip()
                        # 写入数据库
                        try:
                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute(
                                "UPDATE transport SET sn_code=%s WHERE out_order_no=%s",
                                (sn_code, current_order["out_order_no"])
                            )
                            conn.commit()
                            conn.close()

                            current_order["sn_code"] = sn_code
                            sn_entry.value = sn_code
                            tip.value = f"识别成功：{sn_code}\n请上传SN码照片存档"
                            tip.color = ft.Colors.GREEN
                            page.update()

                            # 切换到照片上传
                            nonlocal current_mode
                            current_mode = "upload"
                            refresh_view()
                        except Exception as ex:
                            tip.value = f"保存失败：{str(ex)[:30]}"
                            tip.color = ft.Colors.RED
                            scanner.disabled = False
                            page.update()

                    # 原生扫码控件
                    scanner = ft.BarcodeScanner(
                        on_scan=on_scan_success,
                        width=min(320, page.window_width - 40) if page.window_width else 300,
                        height=320,
                        resolution=ft.BarcodeScannerResolution.MEDIUM
                    )

                    def pick_image_scan(e):
                        tip.value = ""
                        page.update()
                        scanner.pick_image()

                    def go_manual(e):
                        nonlocal current_mode
                        current_mode = "manual"
                        refresh_view()

                    return ft.Column(
                        [
                            scanner,
                            tip,
                            ft.Row(
                                [
                                    ft.TextButton("相册选图识别", on_click=pick_image_scan),
                                    ft.TextButton("识别失败？手动录入", on_click=go_manual)
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                            )
                        ],
                        width=min(320, page.window_width - 40) if page.window_width else 300,
                        spacing=8,
                        scroll=ft.ScrollMode.AUTO
                    )

                # 视图2：扫码成功后，上传SN照片
                def build_upload_view():
                    tip = ft.Text("请拍摄或选择SN照片完成存档", size=12, text_align=ft.TextAlign.CENTER)
                    file_picker = ft.FilePicker()

                    def on_file_pick(e: ft.FilePickerResultEvent):
                        if not e.files:
                            return
                        file_path = e.files[0].path
                        try:
                            with open(file_path, "rb") as f:
                                file_data = f.read()

                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("DELETE FROM erp_files WHERE file_type='sn_photos' AND biz_no=%s",
                                        (current_order["out_order_no"],))
                            cur.execute(
                                "INSERT INTO erp_files (file_type, biz_no, file_name, file_data) VALUES (%s, %s, %s, %s)",
                                ("sn_photos", current_order["out_order_no"], f"SN{current_order['out_order_no']}.jpg",
                                 file_data)
                            )
                            sn_photo_path = f"db:sn_photos:{current_order['out_order_no']}"
                            cur.execute(
                                "UPDATE transport SET sn_photo=%s WHERE out_order_no=%s",
                                (sn_photo_path, current_order["out_order_no"])
                            )
                            conn.commit()
                            conn.close()

                            current_order["sn_photo"] = sn_photo_path
                            sn_photo_status.value = "SN照片: 已上传"
                            sn_photo_status.color = ft.Colors.GREEN

                            show_alert(page, "成功", "SN码与照片均已保存")
                            sn_dialog.open = False
                            dlg.open = False
                            load_trans()
                            page.update()
                        except Exception as ex:
                            show_alert(page, "错误", f"照片上传失败: {str(ex)}")

                    file_picker.on_result = on_file_pick
                    page.overlay.append(file_picker)

                    def pick_photo(e):
                        file_picker.pick_files(
                            allow_multiple=False,
                            file_type=ft.FilePickerFileType.IMAGE
                        )

                    return ft.Column(
                        [
                            ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=48),
                            ft.Text("SN码识别成功", size=16, text_align=ft.TextAlign.CENTER, color=ft.Colors.GREEN),
                            tip,
                            ft.ElevatedButton("上传SN照片", on_click=pick_photo, expand=True)
                        ],
                        width=min(320, page.window_width - 40) if page.window_width else 300,
                        spacing=12,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER
                    )

                # 视图3：手动兜底模式
                def build_manual_view():
                    tip = ft.Text("请先上传SN照片，再输入SN码保存", size=12, text_align=ft.TextAlign.CENTER)
                    sn_input = ft.TextField(label="手动输入SN码", value=current_order["sn_code"], width=280)
                    file_picker = ft.FilePicker()
                    photo_saved = False

                    def on_file_pick(e: ft.FilePickerResultEvent):
                        nonlocal photo_saved
                        if not e.files:
                            return
                        file_path = e.files[0].path
                        try:
                            with open(file_path, "rb") as f:
                                file_data = f.read()

                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("DELETE FROM erp_files WHERE file_type='sn_photos' AND biz_no=%s",
                                        (current_order["out_order_no"],))
                            cur.execute(
                                "INSERT INTO erp_files (file_type, biz_no, file_name, file_data) VALUES (%s, %s, %s, %s)",
                                ("sn_photos", current_order["out_order_no"], f"SN{current_order['out_order_no']}.jpg",
                                 file_data)
                            )
                            sn_photo_path = f"db:sn_photos:{current_order['out_order_no']}"
                            cur.execute(
                                "UPDATE transport SET sn_photo=%s WHERE out_order_no=%s",
                                (sn_photo_path, current_order["out_order_no"])
                            )
                            conn.commit()
                            conn.close()

                            current_order["sn_photo"] = sn_photo_path
                            sn_photo_status.value = "SN照片: 已上传"
                            sn_photo_status.color = ft.Colors.GREEN
                            photo_saved = True
                            tip.value = "照片已保存，请输入SN码后点保存"
                            tip.color = ft.Colors.GREEN
                            page.update()
                        except Exception as ex:
                            show_alert(page, "错误", f"照片上传失败: {str(ex)}")

                    file_picker.on_result = on_file_pick
                    page.overlay.append(file_picker)

                    def pick_photo(e):
                        file_picker.pick_files(
                            allow_multiple=False,
                            file_type=ft.FilePickerFileType.IMAGE
                        )

                    def save_sn_code(e):
                        sn_code = sn_input.value.strip()
                        if not sn_code:
                            show_alert(page, "提示", "请输入SN码")
                            return
                        try:
                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute(
                                "UPDATE transport SET sn_code=%s WHERE out_order_no=%s",
                                (sn_code, current_order["out_order_no"])
                            )
                            conn.commit()
                            conn.close()

                            current_order["sn_code"] = sn_code
                            sn_entry.value = sn_code
                            show_alert(page, "成功", "SN码已保存")
                            sn_dialog.open = False
                            dlg.open = False
                            load_trans()
                            page.update()
                        except Exception as ex:
                            show_alert(page, "错误", f"保存失败: {str(ex)}")

                    def back_to_scan(e):
                        nonlocal current_mode
                        current_mode = "scan"
                        refresh_view()

                    return ft.Column(
                        [
                            ft.Text("手动录入SN码", size=16, weight=ft.FontWeight.BOLD),
                            tip,
                            ft.ElevatedButton("拍摄/选择SN照片", on_click=pick_photo, expand=True),
                            sn_input,
                            ft.Row(
                                [
                                    ft.TextButton("返回扫码", on_click=back_to_scan),
                                    ft.ElevatedButton("保存SN码", on_click=save_sn_code)
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                            )
                        ],
                        width=min(320, page.window_width - 40) if page.window_width else 300,
                        spacing=10,
                        scroll=ft.ScrollMode.AUTO
                    )

                # 构建主弹窗
                sn_dialog = ft.AlertDialog(
                    title=ft.Text("SN码录入"),
                    content=build_scan_view(),
                    modal=True,
                    content_padding=ft.Padding(12, 10, 12, 10),
                    actions=[
                        ft.TextButton("关闭", on_click=lambda e: (setattr(sn_dialog, 'open', False), page.update()))
                    ]
                )

                if page.platform == "android":
                    try:
                        page.request_permission("android.permission.CAMERA")
                        page.request_permission("android.permission.READ_MEDIA_IMAGES")
                    except:
                        pass

                page.overlay.append(sn_dialog)
                sn_dialog.open = True
                page.update()

            # ---------- 上传送货照片（支持拍照/相册，加水印控制） ----------
            def do_upload_home_photo(e):
                biz_no, prefix = get_home_photo_biz_info(order_no, out_order_no)
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute("SELECT cust_name, full_addr FROM sale_main WHERE order_no=%s", (order_no,))
                result = cur.fetchone()
                conn.close()
                if not result:
                    show_alert(page,"错误", "未找到订单信息")
                    return
                cust_name, full_addr = result

                # 让用户选择拍照或相册
                def choose_source():
                    # 拍照：自动加水印
                    def take_photo(e):
                        picker = ft.FilePicker()
                        def on_result(e: ft.FilePickerResultEvent):
                            if e.files:
                                process_image(e.files[0].path, add_watermark=True)
                        picker.on_result = on_result
                        page.overlay.append(picker)
                        page.update()
                        picker.pick_files(
                            allow_multiple=False,
                            file_type=ft.FilePickerFileType.IMAGE,
                            source=ft.FilePickerSource.CAMERA  # 移动端使用相机
                        )
                    # 相册：让用户选择是否加水印
                    def choose_album(e):
                        def ask_watermark():
                            def confirm(watermark):
                                picker = ft.FilePicker()
                                def on_result(e: ft.FilePickerResultEvent):
                                    if e.files:
                                        process_image(e.files[0].path, add_watermark=watermark)
                                picker.on_result = on_result
                                page.overlay.append(picker)
                                page.update()
                                picker.pick_files(
                                    allow_multiple=False,
                                    file_type=ft.FilePickerFileType.IMAGE,
                                    source=ft.FilePickerSource.GALLERY
                                )
                                watermark_dlg.open = False
                            watermark_dlg = ft.AlertDialog(
                                title=ft.Text("是否添加水印？"),
                                content=ft.Text("水印包含订单号、客户、地址等信息"),
                                actions=[
                                    ft.TextButton("添加水印", on_click=lambda _: confirm(True)),
                                    ft.TextButton("不添加", on_click=lambda _: confirm(False)),
                                ]
                            )
                            page.overlay.append(watermark_dlg)
                            watermark_dlg.open = True
                            page.update()
                        ask_watermark()

                    # 弹出选择菜单
                    menu = ft.AlertDialog(
                        title=ft.Text("选择图片来源"),
                        content=ft.Column([
                            ft.TextButton("📷 拍照", on_click=take_photo, width=200),
                            ft.TextButton("🖼️ 相册", on_click=choose_album, width=200),
                        ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        actions=[ft.TextButton("取消", on_click=lambda e: setattr(menu, 'open', False))]
                    )
                    page.overlay.append(menu)
                    menu.open = True
                    page.update()

                def process_image(path, add_watermark):
                    try:
                        from PIL import Image, ImageDraw, ImageFont
                        import datetime
                        img = Image.open(path)
                        if add_watermark:
                            draw = ImageDraw.Draw(img)
                            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            watermark = f"{now}\n订单号: {order_no}\n客户: {cust_name}\n地址: {full_addr}"
                            try:
                                font = ImageFont.truetype("simhei.ttf", 20)
                            except:
                                font = ImageFont.load_default()
                            bbox = draw.textbbox((0, 0), watermark, font=font)
                            text_width = bbox[2] - bbox[0]
                            text_height = bbox[3] - bbox[1]
                            x = 10
                            y = img.height - text_height - 10
                            draw.rectangle(
                                [(x - 5, y - 5), (x + text_width + 5, y + text_height + 5)],
                                fill=(0, 0, 0, 128)
                            )
                            draw.text((x, y), watermark, font=font, fill=(255, 255, 255))
                        from io import BytesIO
                        buf = BytesIO()
                        img.save(buf, format="JPEG")
                        file_data = buf.getvalue()

                        conn = get_db_conn()
                        cur = conn.cursor()
                        cur.execute("DELETE FROM erp_files WHERE file_type='home_photos' AND biz_no=%s", (biz_no,))
                        cur.execute(
                            "INSERT INTO erp_files (file_type, biz_no, file_name, file_data) VALUES (%s, %s, %s, %s)",
                            ("home_photos", biz_no, f"{prefix}{biz_no}.jpg", file_data)
                        )
                        home_photo_path = f"db:home_photos:{biz_no}"
                        cur.execute(
                            "UPDATE transport SET home_photo=%s WHERE out_order_no=%s",
                            (home_photo_path, out_order_no)
                        )
                        conn.commit()
                        conn.close()
                        current_order["home_photo"] = home_photo_path
                        home_photo_status.value = "送货照片: 已上传"
                        home_photo_status.color = ft.Colors.GREEN
                        show_alert(page,"成功", "送货入户照片已上传" + ("（已加水印）" if add_watermark else ""))
                        dlg.open = False
                        load_trans()
                    except Exception as ex:
                        show_alert(page,"错误", f"上传失败: {str(ex)}")

                choose_source()

            # 弹窗内容（自适应）
            content = ft.Column(
                [
                    ft.Text(f"订单: {order_no}", size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(f"客户: {cust_name}  电话: {phone}", size=13),
                    ft.Text(f"地址: {full_addr}", size=13),
                    ft.Text(f"型号: {model}  数量: {t_qty}", size=13),
                    status_label,
                    ft.Row([sn_photo_status, home_photo_status], spacing=10),
                    ft.Divider(height=8),
                    ft.Text("操作", weight=ft.FontWeight.BOLD),
                    ft.Row([sn_entry], spacing=8),
                    ft.Row([trans_date_input, need_delivery_cb], spacing=8),
                    ft.Row([delivery01], spacing=5),
                    ft.Row([delivery02], spacing=5),
                    ft.Row(
                        [
                            ft.IconButton(ft.Icons.CAMERA_ALT, tooltip="上传SN照片", on_click=open_sn_manage_dialog),
                            ft.IconButton(ft.Icons.HOME, tooltip="上传送货照片", on_click=do_upload_home_photo),
                            ft.IconButton(ft.Icons.PHOTO, tooltip="查看SN照片", on_click=do_view_sn_photo),
                            ft.IconButton(ft.Icons.PHOTO_LIBRARY, tooltip="查看送货照片", on_click=do_view_home_photo),
                        ],
                        spacing=6,
                        alignment=ft.MainAxisAlignment.SPACE_EVENLY
                    ),
                    ft.Row(
                        [
                            ft.Button("确认出库", icon=ft.Icons.CHECK, expand=True, on_click=do_confirm_out),
                            ft.Button("确认送达", icon=ft.Icons.LOCAL_SHIPPING, expand=True, on_click=do_confirm_delivered),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
                width=min(page.window_width - 40, 420) if page.window_width else 320,
                height=min(page.window_height - 120, 600) if page.window_height else 500,
            )

            dlg = ft.AlertDialog(
                title=ft.Text("出库操作"),
                content=content,
                actions=[
                    ft.TextButton("关闭", on_click=lambda e: setattr(dlg, 'open', False))
                ],
            )
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        import datetime

        def format_date(val):
            if not val:
                return ""
            if isinstance(val, datetime.datetime):
                return val.strftime("%Y-%m-%d")
            s = str(val)
            if ' ' in s:
                return s.split()[0]
            return s

        def change_status(row):
            order_no = row[2]
            out_order_no = row[3]
            current_st = row[12]
            current_send_date = row[13] or ""
            current_trans_date = row[14] or ""

            status_dropdown = ft.Dropdown(
                label="新状态",
                options=[
                    ft.dropdown.Option("待派单"),
                    ft.dropdown.Option("待出库"),
                    ft.dropdown.Option("已出库"),
                    ft.dropdown.Option("待自提"),
                    ft.dropdown.Option("已自提"),
                    ft.dropdown.Option("已送货入户"),
                ],
                value=current_st,
                width=200,
            )

            send_checkbox = ft.Checkbox(label="修改计划送货日期", value=False)
            send_textfield = ft.TextField(
                label="计划送货日期",
                value=format_date(current_send_date),
                width=150,
                disabled=True,
            )

            trans_checkbox = ft.Checkbox(label="修改实际送货日期", value=False)
            trans_textfield = ft.TextField(
                label="实际送货日期",
                value=format_date(current_trans_date),
                width=150,
                disabled=True,
            )

            def on_send_checkbox_change(e):
                send_textfield.disabled = not send_checkbox.value
                page.update()

            def on_trans_checkbox_change(e):
                trans_textfield.disabled = not trans_checkbox.value
                page.update()

            send_checkbox.on_change = on_send_checkbox_change
            trans_checkbox.on_change = on_trans_checkbox_change

            def save_status_change(e, dlg):
                new_status = status_dropdown.value

                updates = ["status=%s"]
                params = [new_status]

                if send_checkbox.value and send_textfield.value:
                    updates.append("send_date=%s")
                    params.append(send_textfield.value)

                if trans_checkbox.value and trans_textfield.value:
                    updates.append("trans_date=%s")
                    params.append(trans_textfield.value)

                params.extend([order_no, out_order_no])

                conn = get_db_conn()
                if not conn:
                    show_alert(page,"错误", "数据库连接失败")
                    return
                cur = conn.cursor()
                try:
                    sql = f"UPDATE transport SET {', '.join(updates)} WHERE order_no=%s AND out_order_no=%s"
                    cur.execute(sql, params)
                    conn.commit()
                    show_alert(page,"成功", "状态更新完成")
                    dlg.open = False
                    load_trans()
                except Exception as ex:
                    conn.rollback()
                    show_alert(page,"错误", f"更新失败：{str(ex)}")
                finally:
                    conn.close()

            dlg = ft.AlertDialog(
                title=ft.Text("修改订单状态/送货日期"),
                content=ft.Column(
                    [
                        ft.Text(f"当前状态：{current_st}"),
                        status_dropdown,
                        ft.Row([send_checkbox, send_textfield]),
                        ft.Row([trans_checkbox, trans_textfield]),
                    ],
                    spacing=10,
                    tight=True,
                ),
                actions=[
                    ft.TextButton("保存", on_click=lambda e: save_status_change(e, dlg)),
                    ft.TextButton("取消", on_click=lambda e: setattr(dlg, 'open', False)),
                ]
            )

            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        def do_query(e):
            load_trans()

        def do_reset(e):
            status_dropdown.value = "全部"
            start_date.value = ""
            end_date.value = ""
            order_no_input.value = ""
            cust_name_input.value = ""
            load_trans()

        query_btn.on_click = do_query
        reset_btn.on_click = do_reset

        main_content.controls.append(
            ft.Column(
                [
                    ft.Text("运输任务", size=20, weight=ft.FontWeight.BOLD),
                    ft.Row(
                        [
                            status_dropdown,
                            start_date,
                            end_date,
                            order_no_input,
                            cust_name_input,
                            query_btn,
                            reset_btn,
                        ],
                        spacing=8,
                        wrap=True,
                    ),
                    ft.Divider(height=10),
                    trans_list,
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            )
        )
        load_trans()
        page.update()

    # ---------------------------- 安装管理（不变） ----------------------------
    def show_install():
        main_content.controls.clear()

        w1 = get_field_width(page,ratio=2, subtract=60)
        w2 = get_field_width(page,ratio=3, subtract=80)

        status_dropdown = ft.Dropdown(
            label="安装状态",
            width=w1,
            options=[
                ft.dropdown.Option("全部"),
                ft.dropdown.Option("待安装"),
                ft.dropdown.Option("已报装"),
                ft.dropdown.Option("已安装"),
            ],
            value="待安装",
        )

        start_date_field = ft.TextField(
            label="起始日期",
            width=w2,
            value=(date.today() - timedelta(days=30)).strftime("%Y-%m-%d"),
            read_only=True,
        )
        end_date_field = ft.TextField(
            label="结束日期",
            width=w2,
            value=date.today().strftime("%Y-%m-%d"),
            read_only=True,
        )

        def pick_date(field):
            def on_date_selected(e):
                if e.control.value:
                    field.value = e.control.value.strftime("%Y-%m-%d")
                    page.update()

            picker = ft.DatePicker(on_change=on_date_selected)
            page.overlay.append(picker)
            picker.open = True
            page.update()

        start_cal_btn = ft.TextButton("📅", on_click=lambda e: pick_date(start_date_field))
        end_cal_btn = ft.TextButton("📅", on_click=lambda e: pick_date(end_date_field))

        order_input = ft.TextField(label="订单号", width=w2, hint_text="模糊搜索")
        cust_input = ft.TextField(label="客户名称", width=w2, hint_text="模糊搜索")

        install_list = ft.Column(spacing=5, scroll=ft.ScrollMode.AUTO)

        def load_install():
            install_list.controls.clear()
            conn = get_db_conn()
            if not conn:
                install_list.controls.append(ft.Text("数据库连接失败", size=14, color="#ef4444"))
                page.update()
                return

            sql = """
                SELECT 
                    MAX(id) AS id,
                    MAX(order_date) AS order_date,
                    order_no,
                    MAX(cust_name) AS cust_name,
                    MAX(phone) AS phone,
                    MAX(factory) AS factory,
                    model,
                    SUM(i_qty) AS i_qty,
                    MAX(status) AS status,
                    MAX(CASE WHEN is_report=1 THEN '是' ELSE '否' END) AS is_report,
                    MAX(install_team) AS install_team,
                    MAX(install_tel) AS install_tel,
                    MAX(installer01) AS installer01,
                    MAX(installer02) AS installer02,
                    MAX(install_fee) AS install_fee,
                    MAX(fee_remark) AS fee_remark,
                    MAX(install_date) AS install_date,
                    MAX(install_time) AS install_time
                FROM install 
                WHERE 1=1
            """
            params = []

            status = status_dropdown.value
            if status and status != "全部":
                sql += " AND status = %s"
                params.append(status)

            start = start_date_field.value
            end = end_date_field.value
            if status in ["已安装", "已报装"]:
                if start:
                    sql += " AND install_date >= %s"
                    params.append(start)
                if end:
                    sql += " AND install_date <= %s"
                    params.append(end)
            else:
                if start:
                    sql += " AND order_date >= %s"
                    params.append(start)
                if end:
                    sql += " AND order_date <= %s"
                    params.append(end)

            order_no = order_input.value.strip()
            if order_no:
                sql += " AND order_no LIKE %s"
                params.append(f"%{order_no}%")

            cust_name = cust_input.value.strip()
            if cust_name:
                sql += " AND cust_name LIKE %s"
                params.append(f"%{cust_name}%")

            sql += " GROUP BY order_no, model ORDER BY MAX(install_date) DESC, MAX(install_time) DESC, order_no DESC"

            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()

            if not rows:
                install_list.controls.append(ft.Text("没有符合条件的安装记录", size=14, color="#94a3b8"))
                page.update()
                return

            for row in rows:
                install_id = row[0]
                order_no = row[2]
                cust_name = row[3]
                model = row[6]
                qty = row[7]
                status = row[8]
                install_time = str(row[17])[:5] if row[17] else "--:--"

                if status == "待安装":
                    color = "#f59e0b"
                elif status == "已报装":
                    color = "#3b82f6"
                else:
                    color = "#10b981"

                card = ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(f"📦 {order_no}", weight=ft.FontWeight.BOLD, size=14),
                                        ft.Text(f"状态: {status}", color=color, size=12),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Text(f"客户: {cust_name}  |  型号: {model}  |  数量: {qty}", size=12),
                                ft.Text(f"安装日期: {row[16] or '--'}  {install_time}", size=12),
                                ft.Row(
                                    [
                                        ft.Button(
                                            "📞 报装（售后）",
                                            on_click=lambda e, rid=install_id, st=status, order=order_no, mdl=model,
                                                            cust=cust_name, q=qty:
                                            report_install(rid, st, order, mdl, cust, q),
                                        ),
                                        ft.Button(
                                            "✅ 确认安装",
                                            on_click=lambda e, rid=install_id, st=status: confirm_install(rid, st),
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.END,
                                    spacing=10,
                                ),
                            ],
                            spacing=5,
                        ),
                        padding=10,
                    )
                )
                install_list.controls.append(card)

            page.update()

        def report_install(install_id, status, order_no, model, cust_name, qty):
            if status != "待安装":
                page.snack_bar = ft.SnackBar(ft.Text("只能报装待安装订单"), bgcolor="#ef4444")
                page.snack_bar.open = True
                page.update()
                return

            team_tel_dict = {
                "海信售后": "400-6111-111",
                "格力售后": "400-836-5315",
                "海尔售后": "4006-999-999",
                "美的售后": "400-889-9315",
                "小天鹅售后": "400-822-8228"
            }

            team_dropdown = ft.Dropdown(
                label="安装单位",
                width=200,
                options=[ft.dropdown.Option(k) for k in team_tel_dict.keys()],
                value="",
            )
            tel_field = ft.TextField(label="联系电话", width=200, read_only=False)
            fee_field = ft.TextField(label="安装费用", width=200, value="0")
            remark_field = ft.TextField(label="费用备注", width=200)

            def on_team_change(e):
                selected = team_dropdown.value
                if selected in team_tel_dict:
                    tel_field.value = team_tel_dict[selected]
                else:
                    tel_field.value = ""
                page.update()

            team_dropdown.on_change = on_team_change

            def do_report(e):
                team = team_dropdown.value.strip()
                if team and not tel_field.value.strip():
                    if team in team_tel_dict:
                        tel_field.value = team_tel_dict[team]
                        page.update()

                tel = tel_field.value.strip()
                fee = float(fee_field.value or 0) if fee_field.value else 0
                remark = remark_field.value.strip()

                if not team or not tel:
                    page.snack_bar = ft.SnackBar(ft.Text("请选择安装单位并填写联系电话"), bgcolor="#ef4444")
                    page.snack_bar.open = True
                    page.update()
                    return

                conn = get_db_conn()
                if not conn:
                    page.snack_bar = ft.SnackBar(ft.Text("数据库连接失败"), bgcolor="#ef4444")
                    page.snack_bar.open = True
                    page.update()
                    return

                cur = conn.cursor()
                try:
                    sql = "UPDATE install SET status='已报装', install_team=%s, install_tel=%s, install_fee=%s, fee_remark=%s WHERE id=%s"
                    params = (team, tel, fee, remark, install_id)
                    cur.execute(sql, params)
                    rows_affected = cur.rowcount
                    conn.commit()
                    if rows_affected == 0:
                        page.snack_bar = ft.SnackBar(ft.Text(f"⚠️ 未找到 ID={install_id} 的记录，更新失败"),
                                                     bgcolor="#ef4444")
                        page.snack_bar.open = True
                        conn.close()
                        page.update()
                        return
                    page.snack_bar = ft.SnackBar(ft.Text("✅ 报装成功，状态已更新为'已报装'"), bgcolor="#10b981")
                    page.snack_bar.open = True
                except Exception as ex:
                    conn.rollback()
                    page.snack_bar = ft.SnackBar(ft.Text(f"❌ 数据库错误：{ex}"), bgcolor="#ef4444")
                    page.snack_bar.open = True
                    conn.close()
                    page.update()
                    return
                conn.close()

                conn2 = get_db_conn()
                if conn2:
                    cur2 = conn2.cursor()
                    cur2.execute("SELECT full_addr, phone FROM sale_main WHERE order_no=%s", (order_no,))
                    addr_row = cur2.fetchone()
                    conn2.close()
                    full_addr = addr_row[0] if addr_row else "无地址"
                    receiver_phone = addr_row[1] if addr_row else "无电话"
                else:
                    full_addr = "无地址"
                    receiver_phone = "无电话"

                clipboard_text = (
                    f"安装联系人：{receiver_phone}\n"
                    f"客户：{cust_name}\n"
                    f"{model} 共 {qty} 套安装\n"
                    f"地址：{full_addr}\n"
                    f"费用备注：{remark}"
                )

                try:
                    page.set_clipboard(clipboard_text)
                    page.snack_bar = ft.SnackBar(
                        ft.Text("✅ 报装信息已复制到剪贴板", size=14),
                        bgcolor="#10b981",
                    )
                    page.snack_bar.open = True
                except AttributeError:
                    page.snack_bar = ft.SnackBar(
                        ft.Text("⚠️ 剪贴板复制失败，请手动复制", size=14),
                        bgcolor="#f59e0b",
                    )
                    page.snack_bar.open = True

                dialog.open = False
                page.update()
                load_install()

            dialog = ft.AlertDialog(
                title=ft.Text("报装信息"),
                content=ft.Column(
                    [team_dropdown, tel_field, fee_field, remark_field],
                    tight=True,
                    spacing=10,
                ),
                actions=[
                    ft.TextButton("确认", on_click=do_report),
                    ft.TextButton("取消", on_click=lambda e: setattr(dialog, 'open', False) or page.update()),
                ],
            )
            page.overlay.append(dialog)
            dialog.open = True
            page.update()

        def confirm_install(install_id, status):
            if status not in ["待安装", "已报装"]:
                page.snack_bar = ft.SnackBar(ft.Text("只能确认待安装或已报装的订单"), bgcolor="#ef4444")
                page.snack_bar.open = True
                page.update()
                return

            installer_field = ft.TextField(label="安装人", width=200, value="徐连配")
            co_installer_field = ft.TextField(label="共同安装人", width=200, value="麻跃进")
            fee_field = ft.TextField(label="安装费用", width=200, value="0")
            remark_field = ft.TextField(label="费用备注", width=200)

            def do_confirm(e):
                installer = installer_field.value.strip()
                co_installer = co_installer_field.value.strip()
                fee = float(fee_field.value or 0) if fee_field.value else 0
                remark = remark_field.value.strip()

                if not installer:
                    page.snack_bar = ft.SnackBar(ft.Text("请填写安装人"), bgcolor="#ef4444")
                    page.snack_bar.open = True
                    page.update()
                    return

                conn = get_db_conn()
                if not conn:
                    page.snack_bar = ft.SnackBar(ft.Text("数据库连接失败"), bgcolor="#ef4444")
                    page.snack_bar.open = True
                    page.update()
                    return

                cur = conn.cursor()
                try:
                    sql = "UPDATE install SET status='已安装', install_date=%s, installer01=%s, installer02=%s, install_fee=%s, fee_remark=%s WHERE id=%s"
                    params = (date.today(), installer, co_installer, fee, remark, install_id)
                    cur.execute(sql, params)
                    rows_affected = cur.rowcount
                    conn.commit()
                    if rows_affected == 0:
                        page.snack_bar = ft.SnackBar(ft.Text(f"⚠️ 未找到 ID={install_id} 的记录，更新失败"),
                                                     bgcolor="#ef4444")
                        page.snack_bar.open = True
                        conn.close()
                        page.update()
                        return
                    page.snack_bar = ft.SnackBar(ft.Text("✅ 确认安装成功，状态已更新为'已安装'"), bgcolor="#10b981")
                    page.snack_bar.open = True
                except Exception as ex:
                    conn.rollback()
                    page.snack_bar = ft.SnackBar(ft.Text(f"❌ 数据库错误：{ex}"), bgcolor="#ef4444")
                    page.snack_bar.open = True
                    conn.close()
                    page.update()
                    return
                conn.close()

                dialog.open = False
                page.update()
                load_install()

            dialog = ft.AlertDialog(
                title=ft.Text("安装确认"),
                content=ft.Column(
                    [installer_field, co_installer_field, fee_field, remark_field],
                    tight=True,
                    spacing=10,
                ),
                actions=[
                    ft.TextButton("确认", on_click=do_confirm),
                    ft.TextButton("取消", on_click=lambda e: setattr(dialog, 'open', False) or page.update()),
                ],
            )
            page.overlay.append(dialog)
            dialog.open = True
            page.update()

        def on_search(e):
            load_install()

        def on_reset(e):
            status_dropdown.value = "待安装"
            start_date_field.value = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
            end_date_field.value = date.today().strftime("%Y-%m-%d")
            order_input.value = ""
            cust_input.value = ""
            load_install()

        query_row = ft.Row(
            [
                status_dropdown,
                ft.Row([start_date_field, start_cal_btn]),
                ft.Row([end_date_field, end_cal_btn]),
                order_input,
                cust_input,
                ft.Button("🔍 查询", on_click=on_search),
                ft.Button("🔄 重置", on_click=on_reset),
            ],
            alignment=ft.MainAxisAlignment.START,
            spacing=8,
            wrap=True,
        )

        title_row = ft.Row(
            [
                ft.Text("🔧 安装任务", size=20, weight=ft.FontWeight.BOLD),
                ft.TextButton("🔄", on_click=lambda e: load_install(), tooltip="刷新"),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        main_content.controls.append(
            ft.Column(
                [
                    title_row,
                    query_row,
                    install_list,
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            )
        )

        load_install()

    # ---------------------------- 库存管理（详情页适配屏幕） ----------------------------
    def show_stock_detail(model):
        conn = get_db_conn()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute("SELECT factory, spec, qty FROM stock_now WHERE model=%s", (model,))
        row = cur.fetchone()
        conn.close()
        if not row:
            show_alert(page,"提示", "未找到该型号库存信息")
            return
        factory, spec, qty = row
        qty = qty or 0

        start_date_field = ft.TextField(
            label="起始日期",
            width=140,
            value=(datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        )
        end_date_field = ft.TextField(
            label="结束日期",
            width=140,
            value=datetime.now().strftime("%Y-%m-%d")
        )

        def pick_date(field):
            def on_date_selected(e):
                if e.control.value:
                    field.value = e.control.value.strftime("%Y-%m-%d")
                    page.update()

            picker = ft.DatePicker(on_change=on_date_selected)
            page.overlay.append(picker)
            picker.open = True
            page.update()

        start_icon = ft.TextButton("📅", on_click=lambda e: pick_date(start_date_field))
        end_icon = ft.TextButton("📅", on_click=lambda e: pick_date(end_date_field))

        in_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("入库日期")),
                ft.DataColumn(ft.Text("数量")),
                ft.DataColumn(ft.Text("入库价")),
                ft.DataColumn(ft.Text("总金额")),
                ft.DataColumn(ft.Text("库位")),
            ],
            rows=[],
            width=450,
        )
        sale_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("状态")),
                ft.DataColumn(ft.Text("订单号")),
                ft.DataColumn(ft.Text("日期")),
                ft.DataColumn(ft.Text("客户")),
                ft.DataColumn(ft.Text("数量")),
                ft.DataColumn(ft.Text("售价")),
                ft.DataColumn(ft.Text("总价")),
            ],
            rows=[],
            width=550,
        )

        stat_label = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color="#d946ef")

        def load_detail_data(model, start, end):
            in_table.rows.clear()
            sale_table.rows.clear()

            conn = get_db_conn()
            if not conn:
                return
            cur = conn.cursor()

            cur.execute("""
                SELECT in_date, qty, in_price, IFNULL(qty*in_price, 0), location
                FROM stock_in
                WHERE model=%s AND in_date BETWEEN %s AND %s
                ORDER BY in_date DESC
            """, (model, start, end))
            in_total_qty = 0
            in_total_amt = 0
            for r in cur.fetchall():
                in_date, qty, price, amount, location = r
                in_total_qty += qty
                in_total_amt += amount
                in_table.rows.append(
                    ft.DataRow(cells=[
                        ft.DataCell(ft.Text(in_date)),
                        ft.DataCell(ft.Text(str(qty))),
                        ft.DataCell(ft.Text(str(price) if price else "")),
                        ft.DataCell(ft.Text(f"{amount:.2f}" if amount else "")),
                        ft.DataCell(ft.Text(location or "")),
                    ])
                )

            cur.execute("""
                SELECT DISTINCT
                    IFNULL(t.status, '未配送'),
                    si.order_no,
                    m.order_date,
                    m.cust_name,
                    si.qty,
                    si.t_price,
                    IFNULL(si.qty * si.t_price, 0)
                FROM sale_items si
                LEFT JOIN sale_main m ON si.order_no = m.order_no
                LEFT JOIN transport t ON si.order_no = t.order_no AND si.model = t.model
                WHERE si.model=%s AND m.order_date BETWEEN %s AND %s
                ORDER BY m.order_date DESC
            """, (model, start, end))
            sale_total_qty = 0
            sale_total_amt = 0
            for r in cur.fetchall():
                status, order_no, order_date, cust_name, qty, price, amount = r
                sale_total_qty += qty
                sale_total_amt += amount
                sale_table.rows.append(
                    ft.DataRow(cells=[
                        ft.DataCell(ft.Text(status or "")),
                        ft.DataCell(ft.Text(order_no or "")),
                        ft.DataCell(ft.Text(order_date or "")),
                        ft.DataCell(ft.Text(cust_name or "")),
                        ft.DataCell(ft.Text(str(qty))),
                        ft.DataCell(ft.Text(str(price) if price else "")),
                        ft.DataCell(ft.Text(f"{amount:.2f}" if amount else "")),
                    ])
                )
            conn.close()

            in_avg = round(in_total_amt / in_total_qty, 2) if in_total_qty > 0 else 0
            sale_avg = round(sale_total_amt / sale_total_qty, 2) if sale_total_qty > 0 else 0
            profit = round((sale_avg - in_avg) * sale_total_qty, 2) if sale_total_qty > 0 else 0
            stat_label.value = (
                f"入库数量：{in_total_qty} 件 | 入库均价：{in_avg} 元 ｜ "
                f"销售数量：{sale_total_qty} 件 ｜ 销售均价：{sale_avg} 元 ｜ 总毛利：{profit} 元"
            )
            page.update()

        load_detail_data(model, start_date_field.value, end_date_field.value)

        date_row = ft.Row(
            [
                ft.Row([start_date_field, start_icon]),
                ft.Row([end_date_field, end_icon]),
                ft.Button("查询",
                          on_click=lambda e: load_detail_data(model, start_date_field.value, end_date_field.value)),
            ],
            spacing=10,
        )

        in_col = ft.Column(
            [
                ft.Text("入库记录", weight=ft.FontWeight.BOLD),
                ft.Column([in_table], height=280, scroll=ft.ScrollMode.AUTO),
            ],
            width=450,
        )
        sale_col = ft.Column(
            [
                ft.Text("销售记录", weight=ft.FontWeight.BOLD),
                ft.Column([sale_table], height=280, scroll=ft.ScrollMode.AUTO),
            ],
            width=550,
        )

        # 用 Row 包裹两列，自动换行适应屏幕
        content = ft.Column(
            [
                ft.Text(f"型号：{model}  理论库存：{qty}", size=16, weight=ft.FontWeight.BOLD, color="red"),
                date_row,
                ft.Row([in_col, sale_col], spacing=20, wrap=True, scroll=ft.ScrollMode.AUTO),
                stat_label,
            ],
            spacing=10,
            width=min(page.window_width * 0.95, 1100) if page.window_width else 1100,
            height=min(page.window_height * 0.85, 700) if page.window_height else 700,
            scroll=ft.ScrollMode.AUTO,
        )

        dlg = ft.AlertDialog(
            title=ft.Text("库存进销详情"),
            content=content,
            actions=[ft.Button("关闭", on_click=lambda e: setattr(dlg, 'open', False) or page.update())],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def show_stock():
        main_content.controls.clear()

        w1 = get_field_width(page,ratio=2, subtract=60)
        brand_dropdown = ft.Dropdown(
            label="品牌",
            width=w1,
            options=[ft.dropdown.Option("")],
            value="",
        )
        model_textfield = ft.TextField(
            label="型号",
            width=w1,
            hint_text="模糊搜索",
        )
        gap_checkbox = ft.Checkbox(label="仅显示缺口", value=False)

        def load_brands():
            conn = get_db_conn()
            if not conn:
                return
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT factory FROM base_product ORDER BY factory")
            brands = [row[0] for row in cur.fetchall()]
            conn.close()
            brand_dropdown.options = [ft.dropdown.Option("")] + [ft.dropdown.Option(b) for b in brands]
            page.update()

        load_brands()

        stock_list = ft.Column(spacing=5)

        def load_stock():
            stock_list.controls.clear()
            conn = get_db_conn()
            if not conn:
                return

            brand = brand_dropdown.value.strip() if brand_dropdown.value else ""
            model = model_textfield.value.strip()
            only_gap = gap_checkbox.value

            cur = conn.cursor()
            cur.execute("""
                SELECT model, IFNULL(SUM(t_qty), 0)
                FROM transport
                WHERE status IN ('待派单', '待出库')
                GROUP BY model
            """)
            wait_out_dict = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("""
                SELECT model, COUNT(*)
                FROM booth
                WHERE is_real = 1 AND status = '上样中'
                GROUP BY model
            """)
            booth_dict = {row[0]: row[1] for row in cur.fetchall()}

            sql = "SELECT factory, model, spec, qty FROM stock_now WHERE 1=1"
            params = []
            if brand:
                sql += " AND factory = %s"
                params.append(brand)
            if model:
                sql += " AND model LIKE %s"
                params.append(f"%{model}%")
            sql += " ORDER BY factory, model"
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()

            has_data = False
            for row in rows:
                factory, model_name, spec, qty = row
                qty = qty if qty is not None else 0
                wait_out = wait_out_dict.get(model_name, 0)
                booth_use = booth_dict.get(model_name, 0)
                s_qty = qty + wait_out - booth_use

                if only_gap and qty >= 0:
                    continue

                has_data = True
                q_qty_display = abs(int(qty)) if qty < 0 else ""

                if qty < 0:
                    status = "⚠️ 存在缺口"
                    color = "#ff0000"
                elif s_qty == 0:
                    status = "❌ 无库存"
                    color = "#94a3b8"
                elif s_qty < 5:
                    status = "⚠️ 库存不足"
                    color = "#ef4444"
                elif s_qty < 20:
                    status = "🟢 正常"
                    color = "#22c55e"
                else:
                    status = "✅ 充足"
                    color = "#22c55e"

                if model_name in booth_dict:
                    status += " 有样机"

                card = ft.Card(
                    content=ft.Container(
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(model_name, weight=ft.FontWeight.BOLD, size=16),
                                        ft.Text(f"品牌: {factory} | 规格: {spec}", size=12, color="#64748b"),
                                        ft.Text(f"理论: {qty} | 实际: {s_qty} | 缺口: {q_qty_display}", size=12),
                                        ft.Text(status, size=12, color=color),
                                    ],
                                    spacing=2,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        padding=10,
                        on_click=lambda e, m=model_name: show_stock_detail(m),
                    )
                )
                stock_list.controls.append(card)

            if not has_data:
                stock_list.controls.append(ft.Text("没有符合条件的库存", size=14, color="#94a3b8"))

            page.update()

        def on_search(e):
            load_stock()

        def on_refresh(e):
            load_brands()
            load_stock()

        query_row = ft.Row(
            [
                brand_dropdown,
                model_textfield,
                gap_checkbox,
                ft.Button("查询", on_click=on_search),
                ft.TextButton("🔄", on_click=on_refresh, tooltip="刷新"),
            ],
            alignment=ft.MainAxisAlignment.START,
            spacing=10,
            wrap=True,
        )

        main_content.controls.append(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("实时库存", size=20, weight=ft.FontWeight.BOLD),
                            ft.TextButton("🔄", on_click=lambda e: load_stock()),
                        ]
                    ),
                    query_row,
                    stock_list,
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            )
        )

        load_stock()

    def show_more_menu():
        main_content.controls.clear()
        menu_items = ft.Column([
            ft.ListTile(title=ft.Text("产品档案"), leading=ft.Icon(ft.Icons.CATEGORY), on_click=lambda e: show_products()),
            ft.ListTile(title=ft.Text("客户档案"), leading=ft.Icon(ft.Icons.PEOPLE), on_click=lambda e: show_customers()),
            ft.ListTile(title=ft.Text("发票管理"), leading=ft.Icon(ft.Icons.RECEIPT), on_click=lambda e: show_invoice()),
            ft.ListTile(title=ft.Text("补贴申报"), leading=ft.Icon(ft.Icons.MONEY), on_click=lambda e: show_subsidy()),
            ft.ListTile(title=ft.Text("财务管理"), leading=ft.Icon(ft.Icons.ACCOUNT_BALANCE), on_click=lambda e: show_finance()),
            ft.ListTile(title=ft.Text("入库记录查询"), leading=ft.Icon(ft.Icons.HISTORY), on_click=lambda e: show_inbound_records()),
            ft.ListTile(title=ft.Text("销售订单查询"), leading=ft.Icon(ft.Icons.SEARCH), on_click=lambda e: show_sale_orders()),
            ft.ListTile(title=ft.Text("展台样机"), leading=ft.Icon(ft.Icons.DISPLAY_SETTINGS), on_click=lambda e: show_booth()),
            ft.ListTile(title=ft.Text("用户管理"), leading=ft.Icon(ft.Icons.SUPERVISOR_ACCOUNT), on_click=lambda e: show_user_manager()) if current_user and current_user["role"] == "超级管理员" else ft.Container()
        ])
        main_content.controls.append(menu_items)
        page.update()

    # ---------------------------- 产品档案 ----------------------------
    def show_products():
        def load_products():
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT model, factory, spec, price FROM base_product ORDER BY model")
            rows = cur.fetchall()
            conn.close()
            products_list.controls.clear()
            for row in rows:
                products_list.controls.append(
                    ft.Card(content=ft.Container(
                        content=ft.Column([
                            ft.Text(row[0], weight=ft.FontWeight.BOLD),
                            ft.Text(f"品牌: {row[1]} | 规格: {row[2]} | 价格: {row[3]}", size=12)
                        ], spacing=2), padding=10)))
            page.update()
        products_list = ft.Column(spacing=5)
        main_content.controls.clear()
        main_content.controls.append(ft.Column([
            ft.Row([ft.Text("产品档案", size=20, weight=ft.FontWeight.BOLD), ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: load_products())]),
            products_list], scroll=ft.ScrollMode.AUTO))
        load_products()

    # ---------------------------- 客户档案 ----------------------------
    def show_customers():
        def load_customers():
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT name, phone, full_addr, total_amount FROM base_customer ORDER BY total_amount DESC")
            rows = cur.fetchall()
            conn.close()
            customers_list.controls.clear()
            for row in rows:
                customers_list.controls.append(
                    ft.Card(content=ft.Container(
                        content=ft.Column([
                            ft.Text(row[0], weight=ft.FontWeight.BOLD),
                            ft.Text(f"电话: {row[1]}", size=12),
                            ft.Text(f"地址: {row[2]}", size=12),
                            ft.Text(f"累计消费: {row[3]} 元", size=12, color=ft.Colors.GREEN)
                        ], spacing=2), padding=10)))
            page.update()
        customers_list = ft.Column(spacing=5)
        main_content.controls.clear()
        main_content.controls.append(ft.Column([
            ft.Row([ft.Text("客户档案", size=20, weight=ft.FontWeight.BOLD), ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: load_customers())]),
            customers_list], scroll=ft.ScrollMode.AUTO))
        load_customers()

    # ---------------------------- 发票管理 ----------------------------
    def show_invoice():
        main_content.controls.clear()
        invoice_list = ft.Column(spacing=5)
        def load_invoice():
            invoice_list.controls.clear()
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT invoice_no, order_no, cust_name, invoice_amount, invoice_date, status FROM invoice ORDER BY invoice_date DESC")
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                invoice_list.controls.append(
                    ft.Card(content=ft.Container(
                        content=ft.Column([
                            ft.Text(f"发票号: {row[0]}", weight=ft.FontWeight.BOLD),
                            ft.Text(f"订单: {row[1]}  客户: {row[2]}"),
                            ft.Text(f"金额: {row[3]}  日期: {row[4]}  状态: {row[5]}", size=12)
                        ], spacing=2), padding=10)))
            page.update()
        def new_invoice():
            def select_order(e):
                order_no = order_dropdown.value
                if not order_no: return
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute("SELECT SUM(total) FROM sale_items WHERE order_no=%s", (order_no,))
                total = cur.fetchone()[0] or 0
                conn.close()
                invoice_no = gen_invoice_no()
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute("""INSERT INTO invoice (invoice_no, order_no, cust_name, invoice_amount, invoice_date, status, invoice_type)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (invoice_no, order_no, "客户名", total, date.today(), "已开票", "电子发票"))
                conn.commit()
                conn.close()
                page.dialog.open = False
                page.snack_bar = ft.SnackBar(ft.Text(f"发票 {invoice_no} 开具成功"))
                page.snack_bar.open = True
                load_invoice()
                page.update()
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT order_no FROM sale_main WHERE order_no NOT IN (SELECT order_no FROM invoice)")
            orders = [row[0] for row in cur.fetchall()]
            conn.close()
            order_dropdown = ft.Dropdown(label="选择订单", options=[ft.dropdown.Option(o) for o in orders], width=300)
            dialog = ft.AlertDialog(
                title=ft.Text("开具新发票"),
                content=order_dropdown,
                actions=[ft.TextButton("确认", on_click=select_order), ft.TextButton("取消", on_click=lambda e: setattr(dialog, 'open', False))]
            )
            page.dialog = dialog
            dialog.open = True
            page.update()
        main_content.controls.append(
            ft.Column([
                ft.Row([ft.Text("发票管理", size=20, weight=ft.FontWeight.BOLD), ft.IconButton(ft.Icons.ADD, on_click=lambda e: new_invoice()), ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: load_invoice())]),
                invoice_list], scroll=ft.ScrollMode.AUTO))
        load_invoice()

    # ---------------------------- 补贴申报 ----------------------------
    def show_subsidy():
        main_content.controls.clear()
        subsidy_list = ft.Column(spacing=5)
        def load_subsidy():
            subsidy_list.controls.clear()
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT claim_no, order_no, cust_name, claim_amount, status FROM subsidy_claim ORDER BY claim_date DESC")
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                subsidy_list.controls.append(
                    ft.Card(content=ft.Container(
                        content=ft.Column([
                            ft.Text(f"申报单: {row[0]}", weight=ft.FontWeight.BOLD),
                            ft.Text(f"订单: {row[1]}  客户: {row[2]}"),
                            ft.Text(f"金额: {row[3]}  状态: {row[4]}", size=12)
                        ], spacing=2), padding=10)))
            page.update()
        def new_subsidy():
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT order_no FROM sale_main WHERE order_no NOT IN (SELECT order_no FROM subsidy_claim)")
            orders = [row[0] for row in cur.fetchall()]
            conn.close()
            if not orders:
                page.snack_bar = ft.SnackBar(ft.Text("没有可申报的订单"))
                page.snack_bar.open = True
                return
            order_dropdown = ft.Dropdown(label="选择订单", options=[ft.dropdown.Option(o) for o in orders], width=300)
            def do_create(e):
                order_no = order_dropdown.value
                if not order_no: return
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute("SELECT cust_name, card_no, SUM(total) FROM sale_items JOIN sale_main USING(order_no) WHERE order_no=%s", (order_no,))
                cust_name, card_no, total = cur.fetchone()
                claim_no = f"CLM{date.today().strftime('%Y%m%d')}{int(datetime.now().timestamp()) % 10000:04d}"
                cur.execute("""INSERT INTO subsidy_claim (claim_no, order_no, cust_name, card_no, claim_amount, claim_date, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (claim_no, order_no, cust_name, card_no, total, date.today(), "待申报"))
                conn.commit()
                conn.close()
                page.dialog.open = False
                page.snack_bar = ft.SnackBar(ft.Text(f"申报单 {claim_no} 创建成功"))
                page.snack_bar.open = True
                load_subsidy()
                page.update()
            dialog = ft.AlertDialog(
                title=ft.Text("新建补贴申报"),
                content=order_dropdown,
                actions=[ft.TextButton("确认", on_click=do_create), ft.TextButton("取消", on_click=lambda e: setattr(dialog, 'open', False))]
            )
            page.dialog = dialog
            dialog.open = True
            page.update()
        main_content.controls.append(
            ft.Column([
                ft.Row([ft.Text("补贴申报", size=20, weight=ft.FontWeight.BOLD), ft.IconButton(ft.Icons.ADD, on_click=lambda e: new_subsidy()), ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: load_subsidy())]),
                subsidy_list], scroll=ft.ScrollMode.AUTO))
        load_subsidy()

    # ---------------------------- 财务管理 ----------------------------
    def show_finance():
        main_content.controls.clear()
        year_month = ft.Row([
            ft.Dropdown(label="年份", options=[ft.dropdown.Option(str(y)) for y in range(2023, 2035)], value=str(date.today().year)),
            ft.Dropdown(label="月份", options=[ft.dropdown.Option(f"{m:02d}") for m in range(1,13)], value=f"{date.today().month:02d}")
        ])
        result_text = ft.Text("", selectable=True)
        def calc_finance(e):
            year = year_month.controls[0].value
            month = year_month.controls[1].value
            prefix = f"{year}-{month}"
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT IFNULL(SUM(total),0) FROM sale_items JOIN sale_main USING(order_no) WHERE DATE_FORMAT(order_date,'%Y-%m')=%s", (prefix,))
            sale_total = cur.fetchone()[0] or 0
            cur.execute("SELECT IFNULL(SUM(qty*in_price),0) FROM stock_in WHERE DATE_FORMAT(in_date,'%Y-%m')=%s", (prefix,))
            in_cost = cur.fetchone()[0] or 0
            cur.execute("SELECT IFNULL(SUM(amount),0) FROM operate_cost WHERE DATE_FORMAT(cost_date,'%Y-%m')=%s", (prefix,))
            op_cost = cur.fetchone()[0] or 0
            cur.execute("SELECT IFNULL(SUM(install_fee),0) FROM install WHERE DATE_FORMAT(install_date,'%Y-%m')=%s AND status='已安装'", (prefix,))
            inst_fee = cur.fetchone()[0] or 0
            profit = sale_total - in_cost - op_cost - inst_fee
            result_text.value = f"""📅 {year}年{month}月财务统计
销售额: {sale_total:.2f}
进货成本: {in_cost:.2f}
运营成本: {op_cost:.2f}
安装费用: {inst_fee:.2f}
净利润: {profit:.2f}"""
            page.update()
            conn.close()
        main_content.controls.append(
            ft.Column([
                ft.Text("财务报表", size=20, weight=ft.FontWeight.BOLD),
                year_month,
                ft.Button("计算", icon=ft.Icons.CALCULATE, on_click=calc_finance),
                ft.Card(content=ft.Container(content=result_text, padding=15))
            ], spacing=15))
        page.update()

    # ---------------------------- 入库记录查询 ----------------------------
    def show_inbound_records():
        main_content.controls.clear()
        w1 = get_field_width(page,ratio=2, subtract=60)
        start_date = ft.TextField(label="起始日期", hint_text="YYYY-MM-DD", width=w1)
        end_date = ft.TextField(label="结束日期", hint_text="YYYY-MM-DD", width=w1)
        brand = ft.TextField(label="品牌", width=w1)
        model = ft.TextField(label="型号", width=w1)
        query_btn = ft.Button("查询", icon=ft.Icons.SEARCH)
        results_list = ft.Column(spacing=5)
        total_label = ft.Text("", size=14)

        def show_detail(row):
            detail_text = f"""入库详情
ID: {row[0]}
品牌: {row[1]}
品类: {row[2]}
型号: {row[3]}
数量: {row[4]}
单价: {row[5]}
日期: {row[6]}"""
            dialog = ft.AlertDialog(
                title=ft.Text("入库明细"),
                content=ft.Text(detail_text),
                actions=[ft.TextButton("关闭", on_click=lambda e: setattr(dialog, 'open', False))]
            )
            page.dialog = dialog
            dialog.open = True
            page.update()

        def do_query(e):
            results_list.controls.clear()
            conn = get_db_conn()
            if not conn: return
            cur = conn.cursor()
            sql = "SELECT id, factory, category, model, qty, in_price, in_date FROM stock_in WHERE 1=1"
            params = []
            if start_date.value:
                sql += " AND in_date >= %s"
                params.append(start_date.value)
            if end_date.value:
                sql += " AND in_date <= %s"
                params.append(end_date.value)
            if brand.value:
                sql += " AND factory LIKE %s"
                params.append(f"%{brand.value}%")
            if model.value:
                sql += " AND model LIKE %s"
                params.append(f"%{model.value}%")
            sql += " ORDER BY in_date DESC"
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()
            total_qty = 0
            total_amt = 0
            for row in rows:
                total_qty += row[4]
                total_amt += row[4] * (row[5] or 0)
                results_list.controls.append(
                    ft.Card(content=ft.Container(
                        content=ft.Column([
                            ft.Text(f"{row[2]} | {row[1]}  {row[3]}", weight=ft.FontWeight.BOLD),
                            ft.Text(f"数量: {row[4]}  单价: {row[5]}  日期: {row[6]}")
                        ], spacing=2),
                        padding=8,
                        on_click=lambda e, r=row: show_detail(r)
                    ))
                )
            total_label.value = f"总数量: {total_qty}  总金额: {total_amt:.2f}"
            page.update()

        query_btn.on_click = do_query
        main_content.controls.append(
            ft.Column([
                ft.Text("入库记录查询", size=20, weight=ft.FontWeight.BOLD),
                ft.Row([start_date, end_date], alignment=ft.MainAxisAlignment.START),
                ft.Row([brand, model], alignment=ft.MainAxisAlignment.START),
                query_btn,
                total_label,
                results_list
            ], spacing=12, scroll=ft.ScrollMode.AUTO))
        page.update()

    # ---------------------------- 销售订单查询（简版） ----------------------------
    def show_sale_orders():
        main_content.controls.clear()
        w1 = get_field_width(page,ratio=2, subtract=60)
        start_date = ft.TextField(label="起始日期", width=w1)
        end_date = ft.TextField(label="结束日期", width=w1)
        order_no = ft.TextField(label="订单号", width=w1)
        cust_name = ft.TextField(label="客户", width=w1)
        model = ft.TextField(label="型号", width=w1)
        query_btn = ft.Button("查询", icon=ft.Icons.SEARCH)
        orders_list = ft.Column(spacing=5)

        def show_order_detail(order_no):
            conn = get_db_conn()
            if not conn:
                return
            cur = conn.cursor()
            cur.execute("""
                SELECT m.order_no, m.order_date, m.cust_name, m.phone, m.full_addr, i.model, i.qty, i.total 
                FROM sale_main m JOIN sale_items i ON m.order_no=i.order_no 
                WHERE m.order_no=%s
            """, (order_no,))
            rows = cur.fetchall()
            conn.close()
            if not rows:
                page.snack_bar = ft.SnackBar(ft.Text("未找到明细"))
                page.snack_bar.open = True
                page.update()
                return
            detail_text = f"订单号: {rows[0][0]}\n日期: {rows[0][1]}\n客户: {rows[0][2]}\n电话: {rows[0][3]}\n地址: {rows[0][4]}\n\n商品明细:\n"
            for r in rows:
                detail_text += f"型号: {r[5]}  数量: {r[6]}  金额: {r[7]:.2f}\n"
            dialog = ft.AlertDialog(
                title=ft.Text("订单明细"),
                content=ft.Text(detail_text),
                actions=[ft.TextButton("关闭", on_click=lambda e: setattr(dialog, 'open', False))]
            )
            page.dialog = dialog
            dialog.open = True
            page.update()

        def do_query(e):
            orders_list.controls.clear()
            conn = get_db_conn()
            if not conn: return
            cur = conn.cursor()
            sql = """SELECT m.order_no, m.order_date, m.cust_name, m.phone, SUM(i.total) as total
                     FROM sale_main m JOIN sale_items i ON m.order_no=i.order_no WHERE 1=1"""
            params = []
            if start_date.value:
                sql += " AND m.order_date >= %s"
                params.append(start_date.value)
            if end_date.value:
                sql += " AND m.order_date <= %s"
                params.append(end_date.value)
            if order_no.value:
                sql += " AND m.order_no LIKE %s"
                params.append(f"%{order_no.value}%")
            if cust_name.value:
                sql += " AND m.cust_name LIKE %s"
                params.append(f"%{cust_name.value}%")
            if model.value:
                sql += " AND i.model LIKE %s"
                params.append(f"%{model.value}%")
            sql += " GROUP BY m.order_no ORDER BY m.order_date DESC"
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                orders_list.controls.append(
                    ft.Card(content=ft.Container(
                        content=ft.Column([
                            ft.Text(f"订单号: {row[0]}  日期: {row[1]}", weight=ft.FontWeight.BOLD),
                            ft.Text(f"客户: {row[2]}  电话: {row[3]}  金额: {row[4]:.2f}")
                        ], spacing=2),
                        padding=8,
                        on_click=lambda e, order=row[0]: show_order_detail(order)
                    ))
                )
            page.update()

        query_btn.on_click = do_query
        main_content.controls.append(
            ft.Column([
                ft.Text("销售订单查询", size=20, weight=ft.FontWeight.BOLD),
                ft.Row([start_date, end_date], alignment=ft.MainAxisAlignment.START),
                ft.Row([order_no, cust_name], alignment=ft.MainAxisAlignment.START),
                ft.Row([model], alignment=ft.MainAxisAlignment.START),
                query_btn,
                orders_list
            ], spacing=12, scroll=ft.ScrollMode.AUTO))
        page.update()

    # ---------------------------- 展台样机 ----------------------------
    def show_booth():
        main_content.controls.clear()
        booth_grid = ft.GridView(expand=1, runs_count=2, max_extent=200, child_aspect_ratio=0.8, spacing=10)
        def load_booth():
            booth_grid.controls.clear()
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT id, factory, category, model, price, is_real, feature, after_sales, p_website, on_price, on_date FROM booth WHERE status='上样中'")
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                booth_grid.controls.append(
                    ft.Card(content=ft.Container(
                        content=ft.Column([
                            ft.Text(row[3], weight=ft.FontWeight.BOLD, size=14),
                            ft.Text(f"{row[1]} | {row[2]}", size=11),
                            ft.Text(f"备案价: {row[4]}", size=11),
                            ft.Text(f"实机: {'是' if row[5] else '否'}", size=11),
                            ft.Row([
                                ft.IconButton(ft.Icons.EDIT, on_click=lambda e, rid=row[0]: edit_booth(rid)),
                                ft.IconButton(ft.Icons.DELETE, on_click=lambda e, rid=row[0]: remove_booth(rid))
                            ])
                        ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=8)))
            page.update()
        def edit_booth(booth_id):
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT factory, category, model, price, is_real, feature, after_sales, p_website, on_price, on_date FROM booth WHERE id=%s", (booth_id,))
            row = cur.fetchone()
            conn.close()
            if not row: return
            factory, category, model, price, is_real, feature, after_sales, p_website, on_price, on_date = row
            factory_in = ft.TextField(label="品牌", value=factory, width=200)
            category_in = ft.TextField(label="品类", value=category, width=200)
            model_in = ft.TextField(label="型号", value=model, width=200, read_only=True)
            price_in = ft.TextField(label="备案价", value=str(price), width=200)
            is_real_in = ft.Dropdown(label="实机与否", options=[ft.dropdown.Option("是"), ft.dropdown.Option("否")], value="是" if is_real else "否", width=200)
            feature_in = ft.TextField(label="特点", value=feature or "", width=200)
            after_in = ft.TextField(label="售后", value=after_sales or "", width=200)
            web_in = ft.TextField(label="官网", value=p_website or "", width=200)
            online_price_in = ft.TextField(label="线上价", value=str(on_price or 0), width=200)
            on_date_in = ft.TextField(label="上样日期", value=str(on_date), width=200)
            def save_edit(e):
                new_factory = factory_in.value.strip()
                new_category = category_in.value.strip()
                new_price = float(price_in.value or 0)
                new_is_real = 1 if is_real_in.value == "是" else 0
                new_feature = feature_in.value
                new_after = after_in.value
                new_web = web_in.value
                new_online = float(online_price_in.value or 0)
                new_date = on_date_in.value
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute("""UPDATE booth SET factory=%s, category=%s, price=%s, is_real=%s, feature=%s,
                                after_sales=%s, p_website=%s, on_price=%s, on_date=%s, update_time=NOW()
                                WHERE id=%s""",
                            (new_factory, new_category, new_price, new_is_real, new_feature, new_after, new_web, new_online, new_date, booth_id))
                conn.commit()
                conn.close()
                page.dialog.open = False
                page.snack_bar = ft.SnackBar(ft.Text("样机信息已更新"))
                page.snack_bar.open = True
                load_booth()
                page.update()
            dialog = ft.AlertDialog(
                title=ft.Text("编辑样机"),
                content=ft.Column([factory_in, category_in, model_in, price_in, is_real_in, feature_in, after_in, web_in, online_price_in, on_date_in],
                                  tight=True, spacing=8, scroll=ft.ScrollMode.AUTO),
                actions=[ft.TextButton("保存", on_click=save_edit), ft.TextButton("取消", on_click=lambda e: setattr(dialog, 'open', False))]
            )
            page.dialog = dialog
            dialog.open = True
            page.update()
        def remove_booth(booth_id):
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("UPDATE booth SET status='已下样' WHERE id=%s", (booth_id,))
            conn.commit()
            conn.close()
            load_booth()
            page.snack_bar = ft.SnackBar(ft.Text("已下样"))
            page.snack_bar.open = True
            page.update()
        def add_booth(e):
            model_input = ft.TextField(label="型号", width=200)
            scan_btn = ft.IconButton(ft.Icons.CAMERA_ALT, on_click=lambda ev: scan_barcode_from_image(page, on_scan))
            factory_input = ft.TextField(label="品牌", width=200)
            category_input = ft.TextField(label="品类", width=200)
            price_input = ft.TextField(label="备案价", value="0", width=200)
            is_real_input = ft.Dropdown(label="实机与否", options=[ft.dropdown.Option("是"), ft.dropdown.Option("否")], value="否", width=200)
            feature_input = ft.TextField(label="特点", width=200)
            after_input = ft.TextField(label="售后", width=200)
            web_input = ft.TextField(label="官网", width=200)
            online_price_input = ft.TextField(label="线上价", value="0", width=200)
            on_date_input = ft.TextField(label="上样日期", value=date.today().isoformat(), width=200)
            def on_scan(code):
                prod = query_product_by_code(code)
                if prod:
                    model_input.value = prod["model"]
                    factory_input.value = prod["factory"]
                    price_input.value = str(prod["price"])
                    page.update()
                else:
                    def after_add(m):
                        model_input.value = m
                        page.update()
                    add_product_from_scan(page, code, after_add)
            def save_new(e):
                model = model_input.value.strip()
                if not model:
                    page.snack_bar = ft.SnackBar(ft.Text("型号不能为空"))
                    page.snack_bar.open = True
                    return
                factory = factory_input.value.strip()
                category = category_input.value.strip()
                price = float(price_input.value or 0)
                is_real = 1 if is_real_input.value == "是" else 0
                feature = feature_input.value
                after = after_input.value
                web = web_input.value
                online = float(online_price_input.value or 0)
                on_date = on_date_input.value
                conn = get_db_conn()
                cur = conn.cursor()
                cur.execute("""INSERT INTO booth (factory, category, model, price, is_real, feature, after_sales, p_website, on_price, on_date, update_time, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), '上样中')""",
                            (factory, category, model, price, is_real, feature, after, web, online, on_date))
                conn.commit()
                conn.close()
                page.dialog.open = False
                page.snack_bar = ft.SnackBar(ft.Text("样机上样成功"))
                page.snack_bar.open = True
                load_booth()
                page.update()
            dialog = ft.AlertDialog(
                title=ft.Text("新增样机"),
                content=ft.Column([
                    ft.Row([model_input, scan_btn], alignment=ft.MainAxisAlignment.START),
                    factory_input, category_input, price_input, is_real_input, feature_input,
                    after_input, web_input, online_price_input, on_date_input
                ], tight=True, spacing=8, scroll=ft.ScrollMode.AUTO),
                actions=[ft.TextButton("保存", on_click=save_new), ft.TextButton("取消", on_click=lambda e: setattr(dialog, 'open', False))]
            )
            page.dialog = dialog
            dialog.open = True
            page.update()
        main_content.controls.append(
            ft.Column([
                ft.Row([ft.Text("展台样机", size=20, weight=ft.FontWeight.BOLD), ft.IconButton(ft.Icons.ADD, on_click=add_booth), ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: load_booth())]),
                booth_grid
            ], scroll=ft.ScrollMode.AUTO))
        load_booth()

    # ---------------------------- 用户管理（完整版） ----------------------------
    def show_user_manager():
        if current_user and current_user.get("role") != "超级管理员":
            show_alert(page,"提示", "仅超级管理员可访问")
            return

        main_content.controls.clear()

        user_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID")),
                ft.DataColumn(ft.Text("用户名")),
                ft.DataColumn(ft.Text("姓名")),
                ft.DataColumn(ft.Text("角色")),
                ft.DataColumn(ft.Text("有效期")),
                ft.DataColumn(ft.Text("权限")),
            ],
            rows=[],
            width=page.window_width - 20 if page.window_width else 600,
        )

        def load_users():
            user_table.rows.clear()
            conn = get_db_conn()
            if not conn:
                show_alert(page,"错误", "数据库连接失败")
                return
            cur = conn.cursor()
            cur.execute("SELECT id, username, real_name, role, expire_date, permissions FROM users ORDER BY id")
            rows = cur.fetchall()
            conn.close()
            for row in rows:
                user_table.rows.append(
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(str(row[0]))),
                            ft.DataCell(ft.Text(row[1])),
                            ft.DataCell(ft.Text(row[2] or "")),
                            ft.DataCell(ft.Text(row[3] or "")),
                            ft.DataCell(ft.Text(str(row[4]) if row[4] else "永久")),
                            ft.DataCell(ft.Text(row[5] or "")),
                        ],
                        on_select_change=lambda e, r=row: None
                    )
                )
            page.update()

        def add_user_dialog():
            username_field = ft.TextField(label="用户名", width=250)
            realname_field = ft.TextField(label="真实姓名", width=250)
            password_field = ft.TextField(label="密码", password=True, can_reveal_password=True, width=250)
            role_dropdown = ft.Dropdown(
                label="角色",
                options=[
                    ft.dropdown.Option("普通用户"),
                    ft.dropdown.Option("管理员"),
                    ft.dropdown.Option("销售员"),
                    ft.dropdown.Option("配送员"),
                    ft.dropdown.Option("安装员"),
                ],
                value="普通用户",
                width=250,
            )
            day_field = ft.TextField(label="有效天数(留空永久)", width=250, hint_text="数字")

            perm_checkboxes = {}
            perm_col = ft.Column(spacing=5)
            for p in PERMISSIONS:
                cb = ft.Checkbox(label=p, value=True)
                perm_checkboxes[p] = cb
                perm_col.controls.append(cb)

            def save_user(e):
                uname = username_field.value.strip()
                real = realname_field.value.strip()
                pwd = password_field.value.strip()
                role = role_dropdown.value
                day_str = day_field.value.strip()

                if not uname or not pwd:
                    show_alert(page,"提示", "用户名和密码不能为空")
                    return

                expire_date = None
                if day_str.isdigit() and int(day_str) > 0:
                    expire_date = (date.today() + timedelta(days=int(day_str))).strftime("%Y-%m-%d")
                elif day_str == "" or day_str == "0":
                    expire_date = None
                else:
                    show_alert(page,"错误", "有效期请输入数字（0或留空为永久）")
                    return

                selected = [p for p, cb in perm_checkboxes.items() if cb.value]
                perm_str = ",".join(selected)

                conn = get_db_conn()
                if not conn:
                    show_alert(page,"错误", "数据库连接失败")
                    return
                cur = conn.cursor()
                try:
                    cur.execute(
                        "INSERT INTO users (username, password, real_name, role, permissions, expire_date) VALUES (%s, %s, %s, %s, %s, %s)",
                        (uname, md5_pwd(pwd), real, role, perm_str, expire_date)
                    )
                    conn.commit()
                    show_alert(page,"成功", f"用户 {uname} 添加成功")
                    add_dlg.open = False
                    load_users()
                except Exception as ex:
                    conn.rollback()
                    show_alert(page,"错误", f"添加失败: {str(ex)}")
                finally:
                    conn.close()

            add_dlg = ft.AlertDialog(
                title=ft.Text("新增用户"),
                content=ft.Column(
                    [
                        username_field,
                        realname_field,
                        password_field,
                        role_dropdown,
                        day_field,
                        ft.Divider(height=5),
                        ft.Text("功能权限", weight=ft.FontWeight.BOLD),
                        perm_col,
                    ],
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO,
                    width=300,
                ),
                actions=[
                    ft.TextButton("保存", on_click=save_user),
                    ft.TextButton("取消", on_click=lambda e: setattr(add_dlg, 'open', False)),
                ],
            )
            page.overlay.append(add_dlg)
            add_dlg.open = True
            page.update()

        def edit_user_dialog():
            if not user_table.rows:
                show_alert(page,"提示", "没有用户可编辑")
                return

            def do_edit(e):
                uid_str = id_field.value.strip()
                if not uid_str.isdigit():
                    show_alert(page,"错误", "请输入有效ID")
                    return
                uid = int(uid_str)
                conn = get_db_conn()
                if not conn:
                    show_alert(page,"错误", "数据库连接失败")
                    return
                cur = conn.cursor(dictionary=True)
                cur.execute("SELECT id, username, real_name, role, permissions, expire_date FROM users WHERE id=%s", (uid,))
                user = cur.fetchone()
                conn.close()
                if not user:
                    show_alert(page,"错误", f"未找到ID {uid}")
                    return
                if user["role"] == "超级管理员":
                    show_alert(page,"提示", "超级管理员不可编辑")
                    return
                real_field = ft.TextField(label="真实姓名", value=user["real_name"] or "", width=250)
                role_drop = ft.Dropdown(
                    label="角色",
                    options=[
                        ft.dropdown.Option("普通用户"),
                        ft.dropdown.Option("管理员"),
                        ft.dropdown.Option("销售员"),
                        ft.dropdown.Option("配送员"),
                        ft.dropdown.Option("安装员"),
                    ],
                    value=user["role"] or "普通用户",
                    width=250,
                )
                pwd_field = ft.TextField(label="新密码(留空不修改)", password=True, can_reveal_password=True, width=250)
                day_field = ft.TextField(label="有效天数(重新计算，留空保持原日期)", width=250, hint_text="数字或留空")

                perm_checkboxes = {}
                perm_col = ft.Column(spacing=5)
                user_perms = set(user["permissions"].split(",")) if user["permissions"] else set()
                for p in PERMISSIONS:
                    cb = ft.Checkbox(label=p, value=(p in user_perms))
                    perm_checkboxes[p] = cb
                    perm_col.controls.append(cb)

                def save_edit(e):
                    new_real = real_field.value.strip()
                    new_role = role_drop.value
                    new_pwd = pwd_field.value.strip()
                    day_str = day_field.value.strip()

                    new_expire = user["expire_date"]
                    if day_str.isdigit() and int(day_str) > 0:
                        new_expire = (date.today() + timedelta(days=int(day_str))).strftime("%Y-%m-%d")
                    elif day_str == "" or day_str == "0":
                        new_expire = None
                    elif day_str:
                        show_alert(page,"错误", "有效期请输入数字（0或留空为永久）")
                        return

                    selected = [p for p, cb in perm_checkboxes.items() if cb.value]
                    perm_str = ",".join(selected)

                    conn = get_db_conn()
                    if not conn:
                        show_alert(page,"错误", "数据库连接失败")
                        return
                    cur = conn.cursor()
                    try:
                        if new_pwd:
                            cur.execute(
                                "UPDATE users SET real_name=%s, role=%s, password=%s, permissions=%s, expire_date=%s WHERE id=%s",
                                (new_real, new_role, md5_pwd(new_pwd), perm_str, new_expire, uid)
                            )
                        else:
                            cur.execute(
                                "UPDATE users SET real_name=%s, role=%s, permissions=%s, expire_date=%s WHERE id=%s",
                                (new_real, new_role, perm_str, new_expire, uid)
                            )
                        conn.commit()
                        show_alert(page,"成功", "用户信息已更新")
                        edit_dlg.open = False
                        load_users()
                    except Exception as ex:
                        conn.rollback()
                        show_alert(page,"错误", f"更新失败: {str(ex)}")
                    finally:
                        conn.close()

                edit_dlg = ft.AlertDialog(
                    title=ft.Text(f"编辑用户 {user['username']}"),
                    content=ft.Column(
                        [
                            real_field,
                            role_drop,
                            pwd_field,
                            day_field,
                            ft.Divider(height=5),
                            ft.Text("功能权限", weight=ft.FontWeight.BOLD),
                            perm_col,
                        ],
                        spacing=8,
                        scroll=ft.ScrollMode.AUTO,
                        width=300,
                    ),
                    actions=[
                        ft.TextButton("保存", on_click=save_edit),
                        ft.TextButton("取消", on_click=lambda e: setattr(edit_dlg, 'open', False)),
                    ],
                )
                page.overlay.append(edit_dlg)
                edit_dlg.open = True
                page.update()

            id_field = ft.TextField(label="要编辑的用户ID", width=200)
            select_dlg = ft.AlertDialog(
                title=ft.Text("请输入用户ID"),
                content=id_field,
                actions=[
                    ft.TextButton("确定", on_click=do_edit),
                    ft.TextButton("取消", on_click=lambda e: setattr(select_dlg, 'open', False)),
                ],
            )
            page.overlay.append(select_dlg)
            select_dlg.open = True
            page.update()

        def delete_user():
            if not user_table.rows:
                show_alert(page,"提示", "没有用户可删除")
                return

            def do_delete(e):
                uid_str = id_field.value.strip()
                if not uid_str.isdigit():
                    show_alert(page,"错误", "请输入有效ID")
                    return
                uid = int(uid_str)
                conn = get_db_conn()
                if not conn:
                    show_alert(page,"错误", "数据库连接失败")
                    return
                cur = conn.cursor()
                cur.execute("SELECT role FROM users WHERE id=%s", (uid,))
                row = cur.fetchone()
                if not row:
                    show_alert(page,"错误", "用户不存在")
                    conn.close()
                    return
                if row[0] == "超级管理员":
                    show_alert(page,"提示", "无法删除超级管理员")
                    conn.close()
                    return
                def confirm(e):
                    try:
                        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
                        conn.commit()
                        show_alert(page,"成功", "用户已删除")
                        dlg.open = False
                        load_users()
                    except Exception as ex:
                        conn.rollback()
                        show_alert(page,"错误", f"删除失败: {str(ex)}")
                    finally:
                        conn.close()
                confirm_dlg = ft.AlertDialog(
                    title=ft.Text("确认删除"),
                    content=ft.Text(f"确定要删除ID {uid} 吗？此操作不可恢复！"),
                    actions=[
                        ft.TextButton("确定", on_click=confirm),
                        ft.TextButton("取消", on_click=lambda e: setattr(confirm_dlg, 'open', False)),
                    ],
                )
                page.overlay.append(confirm_dlg)
                confirm_dlg.open = True
                page.update()

            id_field = ft.TextField(label="要删除的用户ID", width=200)
            dlg = ft.AlertDialog(
                title=ft.Text("请输入用户ID"),
                content=id_field,
                actions=[
                    ft.TextButton("确定", on_click=do_delete),
                    ft.TextButton("取消", on_click=lambda e: setattr(dlg, 'open', False)),
                ],
            )
            page.overlay.append(dlg)
            dlg.open = True
            page.update()

        btn_row = ft.Row(
            [
                ft.Button("新增用户", on_click=lambda e: add_user_dialog(), bgcolor=ft.Colors.GREEN, color=ft.Colors.WHITE),
                ft.Button("编辑用户", on_click=lambda e: edit_user_dialog(), bgcolor=ft.Colors.BLUE, color=ft.Colors.WHITE),
                ft.Button("删除用户", on_click=lambda e: delete_user(), bgcolor=ft.Colors.RED, color=ft.Colors.WHITE),
                ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: load_users(), tooltip="刷新"),
            ],
            spacing=10,
            wrap=True,
        )

        main_content.controls.append(
            ft.Column(
                [
                    ft.Text("用户管理（超级管理员）", size=20, weight=ft.FontWeight.BOLD),
                    btn_row,
                    ft.Container(
                        content=ft.Column([user_table], scroll=ft.ScrollMode.AUTO),
                        expand=True,
                    ),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            )
        )
        load_users()
        page.update()

    def logout_handler(e):
        # 清空当前登录用户
        global current_user
        current_user = None
        # 清空页面所有控件
        page.controls.clear()
        # 重新加载登录界面容器
        page.add(login_container)
        # 刷新页面
        page.update()
    # ---------------------------- 个人中心 ----------------------------
    def show_profile():
        main_content.controls.clear()
        main_content.controls.append(
            ft.Column([
                ft.Card(content=ft.Container(
                    content=ft.Column([
                        ft.Text(f"用户名: {current_user['username']}", size=16),
                        ft.Text(f"姓名: {current_user['real_name']}", size=16),
                        ft.Text(f"角色: {current_user['role']}", size=16)
                    ], spacing=10), padding=20)),
                ft.Button("退出登录", icon=ft.Icons.LOGOUT, on_click=logout_handler)
            ], spacing=20))
        page.update()

ft.run(main)