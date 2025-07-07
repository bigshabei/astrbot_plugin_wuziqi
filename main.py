import os
import re
import random
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.message_components import Plain, Image
from astrbot.api.event import MessageChain
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from PIL import Image as PILImage, ImageDraw, ImageFont
import numpy as np
import asyncio
import platform


@register("astrbot_plugin_wuziqi", "DITF16（改）", "简易五子棋游戏（全局匹配重构版）", "2.0.0",
          "https://github.com/DITF16/astrbot_plugin_wuziqi")
class WuziqiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.games: Dict[str, dict] = {}
        self.player_to_game: Dict[str, str] = {}
        self.board_size = config.get('board_size', 15) if config else 15
        self.join_timeout = config.get('join_timeout', 120) if config else 120
        self.backup_interval = config.get('backup_interval', 3600) if config else 3600
        self.data_path = StarTools.get_data_dir("astrbot_plugin_wuziqi")
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.rank_file = self.data_path / "rankings.json"
        self.rank_backup_file = self.data_path / "rankings_backup.json"
        self.rankings: Dict[str, Dict[str, int]] = self._load_rankings()
        self.last_backup_time = 0
        self.font_path = Path(__file__).parent / "msyh.ttf"
        self.wait_tasks: Dict[str, asyncio.Task] = {}
        self.peace_requests: Dict[str, dict] = {}
        self.undo_requests: Dict[str, dict] = {}
        self.undo_stats: Dict[str, Dict[str, dict]] = {}
        logger.info("五子棋插件（全局匹配终版）已加载。")

    # region 数据持久化
    def _load_rankings(self) -> Dict[str, Dict[str, int]]:
        if self.rank_file.exists():
            try:
                with open(self.rank_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载排行榜数据时出错: {e}")
                return {}
        return {}

    def _save_rankings(self):
        try:
            with open(self.rank_file, 'w', encoding='utf-8') as f:
                json.dump(self.rankings, f, ensure_ascii=False, indent=2)
            current_time = int(time.time())
            if current_time - self.last_backup_time >= self.backup_interval:
                self._backup_rankings()
                self.last_backup_time = current_time
        except Exception as e:
            logger.error(f"保存排行榜数据时出错: {e}")

    def _backup_rankings(self):
        try:
            with open(self.rank_backup_file, 'w', encoding='utf-8') as f:
                json.dump(self.rankings, f, ensure_ascii=False, indent=2)
            logger.info(f"排行榜数据已备份到 {self.rank_backup_file}")
        except Exception as e:
            logger.error(f"备份排行榜数据时出错: {e}")

    def _update_rankings(self, winner_id: str, winner_name: str, loser_id: str, loser_name: str):
        if winner_id != "AI":
            if winner_id not in self.rankings:
                self.rankings[winner_id] = {"name": winner_name, "wins": 0, "losses": 0, "draws": 0}
            self.rankings[winner_id]["wins"] += 1
        if loser_id != "AI":
            if loser_id not in self.rankings:
                self.rankings[loser_id] = {"name": loser_name, "wins": 0, "losses": 0, "draws": 0}
            self.rankings[loser_id]["losses"] += 1
        self._save_rankings()

    def _update_draw_rankings(self, player1_id: str, player1_name: str, player2_id: str, player2_name: str):
        if player1_id != "AI":
            if player1_id not in self.rankings:
                self.rankings[player1_id] = {"name": player1_name, "wins": 0, "losses": 0, "draws": 0}
            self.rankings[player1_id]["draws"] += 1
        if player2_id != "AI":
            if player2_id not in self.rankings:
                self.rankings[player2_id] = {"name": player2_name, "wins": 0, "losses": 0, "draws": 0}
            self.rankings[player2_id]["draws"] += 1
        self._save_rankings()

    # endregion

    # region 游戏核心逻辑
    def _init_board(self) -> np.ndarray:
        return np.zeros((self.board_size, self.board_size), dtype=int)

    def _is_valid_move(self, board: np.ndarray, x: int, y: int) -> bool:
        return 0 <= x < self.board_size and 0 <= y < self.board_size and board[x, y] == 0

    def _check_win(self, board: np.ndarray, x: int, y: int, player: int) -> bool:
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]
        for dx, dy in directions:
            count = 1
            for i in range(1, 5):
                nx, ny = x + i * dx, y + i * dy
                if 0 <= nx < self.board_size and 0 <= ny < self.board_size and board[nx, ny] == player:
                    count += 1
                else:
                    break
            for i in range(1, 5):
                nx, ny = x - i * dx, y - i * dy
                if 0 <= nx < self.board_size and 0 <= ny < self.board_size and board[nx, ny] == player:
                    count += 1
                else:
                    break
            if count >= 5: return True
        return False

    def _check_draw(self, board: np.ndarray) -> bool:
        return np.all(board != 0)

    def _count_line(self, board: np.ndarray, x: int, y: int, dx: int, dy: int, player: int) -> Tuple[int, bool]:
        count = 0
        open_ends = 0

        for i in range(1 - 5, 5):
            nx, ny = x + i * dx, y + i * dy
            if not (0 <= nx < self.board_size and 0 <= ny < self.board_size):
                continue
            if board[nx, ny] == player:
                count += 1
            elif board[nx, ny] == 0:
                if i > 0 and board[x + (i - 1) * dx, y + (i - 1) * dy] == player:
                    open_ends += 1
                elif i < 0 and board[x + (i + 1) * dx, y + (i + 1) * dy] == player:
                    open_ends += 1
        return count, open_ends >= 2

    def _evaluate_position(self, board: np.ndarray, x: int, y: int, player: int) -> int:
        score = 0
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]

        temp_board = board.copy()
        temp_board[x, y] = player

        threes, fours = 0, 0
        for dx, dy in directions:
            count, is_live = self._count_line(temp_board, x, y, dx, dy, player)
            if count >= 5: return 100000
            if count == 4: fours += 1
            if count == 3 and is_live: threes += 1

        if fours > 0 or threes > 1: return 10000
        score += threes * 1000

        opponent = 3 - player
        temp_board[x, y] = opponent

        threes, fours = 0, 0
        for dx, dy in directions:
            count, is_live = self._count_line(temp_board, x, y, dx, dy, opponent)
            if count >= 5: score += 50000
            if count == 4: fours += 1
            if count == 3 and is_live: threes += 1

        if fours > 0 or threes > 1: score += 5000
        score += threes * 500

        return score + (self.board_size - (abs(x - self.board_size // 2) + abs(y - self.board_size // 2)))

    def _ai_move(self, game_id: str) -> Optional[Tuple[int, int]]:
        game = self.games.get(game_id)
        if not game: return None
        board = game["board"]
        current_player = game["current_player"]

        best_move, max_score = None, -1
        for r in range(self.board_size):
            for c in range(self.board_size):
                if board[r, c] == 0:
                    score = self._evaluate_position(board, r, c, current_player)
                    if score > max_score:
                        max_score = score
                        best_move = (r, c)

        logger.info(f"AI Move for Game {game_id}: {best_move} with score {max_score}")
        return best_move

    # endregion

    # region 辅助函数
    def _draw_board(self, board: np.ndarray, last_move: Optional[Tuple[int, int]] = None,
                    game_id: str = "default") -> str:
        cell_size, margin = 40, 40
        size = self.board_size * cell_size + 2 * margin
        image = PILImage.new("RGB", (size, size), (220, 220, 220))
        draw = ImageDraw.Draw(image)
        font = self._get_system_font(20)

        board_end = margin + (self.board_size - 1) * cell_size
        for i in range(self.board_size):
            x = margin + i * cell_size
            draw.line([(x, margin), (x, board_end)], fill="black")
            draw.line([(margin, x), (board_end, x)], fill="black")

        star_points = [(3, 3), (11, 3), (3, 11), (11, 11), (7, 7)]
        for sx, sy in star_points:
            cx, cy = margin + sx * cell_size, margin + sy * cell_size
            draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill="black")

        for r in range(self.board_size):
            for c in range(self.board_size):
                if board[r, c] != 0:
                    cx, cy = margin + c * cell_size, margin + r * cell_size
                    color = "black" if board[r, c] == 1 else "white"
                    draw.ellipse((cx - 15, cy - 15, cx + 15, cy + 15), fill=color, outline="gray")
                    if last_move and last_move == (r, c):
                        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill="red")

        for i in range(self.board_size):
            col_label, row_label = str(i + 1), chr(65 + i)
            draw.text((margin + i * cell_size, margin - 25), col_label, fill="black", font=font, anchor="ms")
            draw.text((margin - 25, margin + i * cell_size), row_label, fill="black", font=font, anchor="rm")

        image_path = str(self.data_path / f"board_{game_id}.png")
        image.save(image_path)
        return image_path

    def _draw_rankings_image(self) -> str:
        sorted_rankings = sorted(self.rankings.items(), key=lambda x: x[1]["wins"], reverse=True)[:10]
        if not sorted_rankings: return ""

        title_height, cell_height, margin = 50, 40, 20
        cell_widths = [60, 150, 80, 80, 80, 100, 100]
        total_width = sum(cell_widths)
        total_height = title_height + cell_height * (len(sorted_rankings) + 1)
        image = PILImage.new("RGB", (total_width + margin * 2, total_height + margin * 2), (255, 255, 255))
        draw, font, title_font = ImageDraw.Draw(image), self._get_system_font(20), self._get_system_font(24)

        draw.text((total_width // 2 + margin, margin + title_height // 2), "五子棋排行榜", fill="black",
                  font=title_font, anchor="mm")

        headers = ["排名", "玩家", "胜", "平", "负", "总局", "胜率"]
        x_pos, y_pos = margin, margin + title_height
        for i, header in enumerate(headers):
            draw.text((x_pos + cell_widths[i] // 2, y_pos + cell_height // 2), header, fill="black", font=font,
                      anchor="mm")
            x_pos += cell_widths[i]

        y_pos += cell_height
        for i, (player_id, data) in enumerate(sorted_rankings, 1):
            wins, losses, draws = data.get("wins", 0), data.get("losses", 0), data.get("draws", 0)
            total = wins + losses + draws
            win_rate = f"{(wins / total * 100):.1f}%" if total > 0 else "N/A"
            row_data = [str(i), data.get('name', '未知'), str(wins), str(draws), str(losses), str(total), win_rate]
            x_pos = margin
            for j, text in enumerate(row_data):
                draw.text((x_pos + cell_widths[j] // 2, y_pos + cell_height // 2), text, fill="black", font=font,
                          anchor="mm")
                x_pos += cell_widths[j]
            y_pos += cell_height

        image_path = str(self.data_path / f"rankings_{int(time.time())}.png")
        image.save(image_path)
        return image_path

    def _get_system_font(self, size: int) -> ImageFont:
        try:
            if self.font_path.exists(): return ImageFont.truetype(str(self.font_path), size)
            if platform.system() == "Windows":
                font_path = "C:/Windows/Fonts/simhei.ttf"
            elif platform.system() == "Darwin":
                font_path = "/System/Library/Fonts/PingFang.ttc"
            else:
                font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            return ImageFont.truetype(font_path, size)
        except Exception as e:
            logger.error(f"加载系统字体失败: {e}");
            return ImageFont.load_default()

    def _parse_position(self, text: str) -> Optional[Tuple[int, int]]:
        text = text.strip().upper()
        match = re.match(r'^([A-O])(1[0-5]|[1-9])$', text)
        if match:
            row_char, col_str = match.groups()
            row, col = ord(row_char) - ord('A'), int(col_str) - 1
            return (row, col)
        return None

    def _generate_game_id(self) -> str:
        while True:
            game_id = str(random.randint(1000, 9999))
            if game_id not in self.games: return game_id

    def _get_game_by_player(self, player_id: str) -> Optional[dict]:
        game_id = self.player_to_game.get(player_id)
        if not game_id: return None
        game = self.games.get(game_id)
        if not game:
            del self.player_to_game[player_id]
            return None
        return game

    def _cleanup_game_state(self, game_id: str):
        game = self.games.pop(game_id, None)
        if game:
            for player_num in [1, 2]:
                player_info = game["players"].get(player_num)
                if player_info and player_info["id"] in self.player_to_game:
                    del self.player_to_game[player_info["id"]]

        if game_id in self.undo_stats: del self.undo_stats[game_id]
        if game_id in self.wait_tasks: self.wait_tasks.pop(game_id).cancel()
        if game_id in self.peace_requests: self.peace_requests.pop(game_id, {}).get("timeout_task",
                                                                                    asyncio.Future()).cancel()
        if game_id in self.undo_requests: self.undo_requests.pop(game_id, {}).get("timeout_task",
                                                                                  asyncio.Future()).cancel()

        logger.info(f"游戏状态已清理, Game ID: {game_id}")

    # endregion

    # region 命令处理函数
    @filter.command("五子棋")
    async def start_game(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        if self._get_game_by_player(sender_id):
            yield event.plain_result("您已在游戏中，请先完成或结束对局。")
            return

        game_id = self._generate_game_id()
        self.player_to_game[sender_id] = game_id
        self.games[game_id] = {
            "id": game_id, "board": self._init_board(), "current_player": 1, "last_move": None,
            "players": {1: {"id": sender_id, "name": event.get_sender_name()}, 2: None},
            "history": [], "status": "pending", "creator_context": event.unified_msg_origin
        }
        self.undo_stats[game_id] = {}
        task = asyncio.create_task(self._wait_for_join_timeout(game_id))
        self.wait_tasks[game_id] = task
        logger.info(f"新游戏创建, ID: {game_id}, 发起者: {event.get_sender_name()}({sender_id})")
        yield event.plain_result(
            f"五子棋游戏已创建！游戏ID是【{game_id}】。\n"
            f"让朋友使用 '/加入五子棋 {game_id}' 加入，或您使用 '/人机对战' 与AI开始。\n"
            f"邀请在 {self.join_timeout} 秒后失效。"
        )

    async def _wait_for_join_timeout(self, game_id: str):
        await asyncio.sleep(self.join_timeout)
        game = self.games.get(game_id)
        if game and game["status"] == "pending":
            message_to_send = MessageChain([Plain(f"游戏【{game_id}】因等待玩家超时而被自动取消。")])
            await self.context.send_message(game["creator_context"], message_to_send)
            self._cleanup_game_state(game_id)

    @filter.command("加入五子棋")
    async def join_game(self, event: AstrMessageEvent, game_id: str):
        sender_id = event.get_sender_id()
        if not game_id or not game_id.isdigit():
            yield event.plain_result("指令格式错误，请使用 '/加入五子棋 <游戏ID>'。");
            return
        if self._get_game_by_player(sender_id):
            yield event.plain_result("您已在游戏中，无法加入。");
            return
        game = self.games.get(game_id)
        if not game or game["status"] != "pending":
            yield event.plain_result(f"游戏【{game_id}】不存在、已开始或已结束。");
            return
        if game["players"][1]["id"] == sender_id:
            yield event.plain_result("您不能加入自己创建的游戏。");
            return

        if game_id in self.wait_tasks: self.wait_tasks.pop(game_id).cancel()
        game["players"][2] = {"id": sender_id, "name": event.get_sender_name()}
        game["status"] = "active"
        self.player_to_game[sender_id] = game_id

        p1, p2 = game["players"][1], game["players"][2]
        logger.info(f"玩家 {p2['name']} 加入游戏 {game_id}，对手是 {p1['name']}")

        board_path = self._draw_board(game["board"], game_id=game_id)
        msg = f"{p2['name']} 已加入游戏【{game_id}】，对战开始！\n黑方: {p1['name']}\n白方: {p2['name']}\n\n轮到黑方落子。"

        msg_components_list = [Plain(msg), Image.fromFileSystem(board_path)]
        message_to_send = MessageChain(msg_components_list)

        await self.context.send_message(game['creator_context'], message_to_send)
        yield event.chain_result(msg_components_list)

    @filter.command("人机对战")
    async def start_ai_game(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        game = self._get_game_by_player(sender_id)

        if not game:
            game_id = self._generate_game_id()
            self.player_to_game[sender_id] = game_id
            self.games[game_id] = {
                "id": game_id, "board": self._init_board(), "current_player": 1, "last_move": None,
                "players": {1: {"id": sender_id, "name": event.get_sender_name()},
                            2: {"id": "AI", "name": "AI 玩家", "is_ai": True}},
                "history": [], "status": "active", "creator_context": event.unified_msg_origin
            }
            self.undo_stats[game_id] = {}
            logger.info(f"新的人机对局开始, ID: {game_id}, 玩家: {event.get_sender_name()}")
            yield event.plain_result(f"与AI的对局已开始！ID:【{game_id}】\n您是黑方，请先落子。")
            yield event.image_result(self._draw_board(self.games[game_id]["board"], game_id=game_id))
            return

        if game["status"] == "pending" and game["players"][1]["id"] == sender_id:
            if game["id"] in self.wait_tasks: self.wait_tasks.pop(game["id"]).cancel()
            game["players"][2] = {"id": "AI", "name": "AI 玩家", "is_ai": True}
            game["status"] = "active"
            logger.info(f"游戏 {game['id']} 转为人机模式。")
            yield event.plain_result(f"已匹配AI！游戏【{game['id']}】开始，您是黑方，请落子。")
            yield event.image_result(self._draw_board(game["board"], game_id=game['id']))
            return

        yield event.plain_result("您已在进行中的对局里，无法开始人机对战。")

    @filter.command("取消五子棋")
    async def cancel_game(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        game = self._get_game_by_player(sender_id)
        if not game:
            yield event.plain_result("您没有正在创建或进行中的游戏。");
            return
        if not (game["status"] == "pending" and game["players"][1]["id"] == sender_id):
            yield event.plain_result("只能取消由您发起且未开始的游戏。");
            return
        self._cleanup_game_state(game["id"])
        yield event.plain_result("游戏已取消。")

    @filter.regex(r'^[A-Oa-o](1[0-5]|[1-9])$', flags=re.IGNORECASE)
    async def handle_coordinate_move(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        game = self._get_game_by_player(sender_id)
        if game and game['status'] == 'active':
            async for result in self._handle_move(event, game, event.message_str.strip()): yield result

    @filter.command("落子")
    async def make_move(self, event: AstrMessageEvent, position: str):
        sender_id = event.get_sender_id()
        game = self._get_game_by_player(sender_id)
        if not game: yield event.plain_result("您不在任何对局中。"); return
        if game['status'] != 'active': yield event.plain_result("游戏尚未开始。"); return
        async for result in self._handle_move(event, game, position): yield result

    async def _handle_move(self, event: AstrMessageEvent, game: dict, position_str: str):
        sender_id = event.get_sender_id()
        game_id = game["id"]
        current_player_num = game["current_player"]
        player_data = game["players"][current_player_num]

        if player_data["id"] != sender_id:
            yield event.plain_result(
                f"当前轮到 {player_data['name']} ({'黑棋' if current_player_num == 1 else '白棋'})。");
            return

        pos = self._parse_position(position_str)
        if not pos: yield event.plain_result("坐标格式错误，请使用如 'A1', 'H7' 的格式。"); return
        row, col = pos
        if not self._is_valid_move(game["board"], row, col): yield event.plain_result(
            "无效落子，该位置已有棋子或越界。"); return

        game["board"][row, col] = current_player_num
        game["last_move"] = (row, col)
        game["history"].append(
            {"player": current_player_num, "position": position_str.upper(), "board": game["board"].copy()})
        logger.info(f"Game {game_id}: 玩家 {player_data['name']} 落子于 {position_str.upper()}")

        human_move_message = f"{'黑棋' if current_player_num == 1 else '白棋'} ({player_data['name']}) 落子于 {position_str.upper()}。"

        if self._check_win(game["board"], row, col, current_player_num):
            winner, loser = player_data, game["players"][3 - current_player_num]
            board_path = self._draw_board(game["board"], game["last_move"], game_id)
            yield event.chain_result(
                [Plain(f"{human_move_message}\n游戏结束！{winner['name']} 获胜！"), Image.fromFileSystem(board_path)])
            self._update_rankings(winner['id'], winner['name'], loser['id'], loser['name'])
            self._cleanup_game_state(game_id)
            return

        if self._check_draw(game["board"]):
            p1, p2 = game["players"][1], game["players"][2]
            board_path = self._draw_board(game["board"], game["last_move"], game_id)
            yield event.chain_result(
                [Plain(f"{human_move_message}\n游戏结束！棋盘已满，双方平局！"), Image.fromFileSystem(board_path)])
            self._update_draw_rankings(p1['id'], p1['name'], p2['id'], p2['name'])
            self._cleanup_game_state(game_id)
            return

        game["current_player"] = 3 - game["current_player"]

        if game["players"][game["current_player"]].get("is_ai"):
            await asyncio.sleep(1)
            ai_move = self._ai_move(game_id)
            if ai_move:
                ai_row, ai_col = ai_move
                game["board"][ai_row, ai_col] = game["current_player"]
                game["last_move"] = (ai_row, ai_col)
                ai_pos_str = f"{chr(65 + ai_row)}{ai_col + 1}"
                game["history"].append(
                    {"player": game["current_player"], "position": ai_pos_str, "board": game["board"].copy()})

                ai_player_data = game["players"][game["current_player"]]
                ai_move_message = f"{'白棋' if game['current_player'] == 2 else '黑棋'} ({ai_player_data['name']}) 落子于 {ai_pos_str}。"
                board_path = self._draw_board(game["board"], game["last_move"], game_id)

                if self._check_win(game["board"], ai_row, ai_col, game["current_player"]):
                    winner, loser = ai_player_data, game["players"][3 - game["current_player"]]
                    yield event.chain_result(
                        [Plain(f"{human_move_message}\n{ai_move_message}\n游戏结束！{winner['name']} 获胜！"),
                         Image.fromFileSystem(board_path)])
                    self._update_rankings(winner['id'], winner['name'], loser['id'], loser['name']);
                    self._cleanup_game_state(game_id);
                    return

                yield event.chain_result(
                    [Plain(f"{human_move_message}\n{ai_move_message}"), Image.fromFileSystem(board_path)])
                game["current_player"] = 3 - game["current_player"]
            else:
                yield event.plain_result(f"{human_move_message}\nAI无法找到合适的落子点，游戏出现异常。")
        else:
            board_path = self._draw_board(game["board"], game["last_move"], game_id)
            next_player = game["players"][game["current_player"]]
            yield event.chain_result([Plain(
                f"{human_move_message}\n轮到 {'黑棋' if game['current_player'] == 1 else '白棋'} ({next_player['name']}) 落子。"),
                                      Image.fromFileSystem(board_path)])

    @filter.command("认输")
    async def handle_surrender(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        game = self._get_game_by_player(sender_id)
        if not game or game["status"] != "active": return

        loser_num = 1 if game["players"][1]["id"] == sender_id else 2
        winner_num = 3 - loser_num
        loser, winner = game["players"][loser_num], game["players"][winner_num]

        yield event.plain_result(
            f"{loser['name']} ({'黑棋' if loser_num == 1 else '白棋'}) 认输！\n胜者是: {winner['name']} ({'黑棋' if winner_num == 1 else '白棋'})")
        self._update_rankings(winner['id'], winner['name'], loser['id'], loser['name'])
        self._cleanup_game_state(game['id'])

    @filter.command("结束下棋")
    async def end_game(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        game = self._get_game_by_player(sender_id)
        if not game: yield event.plain_result("您不在任何对局中。"); return
        self._cleanup_game_state(game['id'])
        yield event.plain_result("对局已由玩家结束，无胜负记录。")

    @filter.command("强制结束游戏")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def force_end_game(self, event: AstrMessageEvent, game_id: str):
        if not game_id or game_id not in self.games: yield event.plain_result(f"未找到ID为【{game_id}】的游戏。"); return
        self._cleanup_game_state(game_id)
        yield event.plain_result(f"管理员已强制结束游戏【{game_id}】。")

    @filter.command("五子棋帮助")
    async def show_help(self, event: AstrMessageEvent):
        # FIXED: 移除了多余的 'h'
        yield event.plain_result(
            "🎲 五子棋游戏帮助（全局匹配版） 🎲\n\n"
            "【核心指令】\n"
            "- /五子棋: 创建新游戏，获取游戏ID。\n"
            "- /加入五子棋 <ID>: 输入ID加入游戏。\n"
            "- /人机对战: 直接开始或加入人机对战。\n"
            "- 落子 <坐标> 或直接发坐标(如H7): 落子。\n\n"
            "【游戏内指令】\n"
            "- /查看棋局: 查看当前棋盘。\n"
            "- /悔棋, /接受悔棋, /拒绝悔棋\n"
            "- /求和, /接受求和, /拒绝求和\n"
            "- /认输: 结束游戏并判负。\n"
            "- /结束下棋: 放弃对局（无胜负记录）。\n\n"
            "【其他】\n"
            "- /我的战绩 & /五子棋排行榜: 查询战绩。\n"
            "- /强制结束游戏 <ID>: [管理员]强制结束游戏。"
        )

    @filter.command("五子棋排行榜")
    async def show_rankings(self, event: AstrMessageEvent):
        if not self.rankings: yield event.plain_result("暂无玩家上榜。"); return
        image_path = self._draw_rankings_image()
        if image_path:
            yield event.image_result(image_path)
        else:
            yield event.plain_result("排行榜为空或生成图片失败。")

    @filter.command("我的战绩")
    async def show_my_stats(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        if sender_id not in self.rankings: yield event.plain_result("您还没有战绩数据。"); return
        data = self.rankings[sender_id]
        wins, losses, draws = data.get("wins", 0), data.get("losses", 0), data.get("draws", 0)
        total = wins + losses + draws
        win_rate = (wins / total * 100) if total > 0 else 0
        yield event.plain_result(
            f"您的五子棋战绩 [{data['name']}]：\n胜：{wins} | 负：{losses} | 平：{draws}\n总对局：{total} | 胜率：{win_rate:.2f}%")

    # endregion

    async def terminate(self):
        for task in self.wait_tasks.values(): task.cancel()
        for req in self.peace_requests.values():
            if "timeout_task" in req: req["timeout_task"].cancel()
        for req in self.undo_requests.values():
            if "timeout_task" in req: req["timeout_task"].cancel()
        self._save_rankings()
        logger.info("五子棋插件已卸载，所有游戏和任务已清理。")