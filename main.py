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
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from PIL import Image as PILImage, ImageDraw, ImageFont
import numpy as np
import asyncio
import platform

@register("astrbot_plugin_wuziqi", "大沙北", "简易五子棋游戏", "1.3.4", "https://github.com/bigshabei/astrbot_plugin_wuziqi")
class WuziqiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.games: Dict[str, dict] = {}
        # 从配置中读取棋盘大小、等待超时时间和备份间隔时间
        self.board_size = config.get('board_size', 15) if config else 15
        self.join_timeout = config.get('join_timeout', 120) if config else 120
        self.backup_interval = config.get('backup_interval', 3600) if config else 3600
        # 存储路径与插件名一致
        self.data_path = Path("data/plugins_data/astrbot_plugin_wuziqi")
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.rank_file = self.data_path / "rankings.json"
        self.rank_backup_file = self.data_path / "rankings_backup.json"
        self.wait_tasks: Dict[str, asyncio.Task] = {}
        self.peace_requests: Dict[str, dict] = {}
        self.undo_requests: Dict[str, dict] = {}
        self.undo_stats: Dict[str, Dict[str, dict]] = {}
        self.rankings: Dict[str, Dict[str, int]] = self._load_rankings()
        self.last_backup_time = 0
        self.font_path = Path(__file__).parent / "msyh.ttf"

    def _load_rankings(self) -> Dict[str, Dict[str, int]]:
        if self.rank_file.exists():
            try:
                with open(self.rank_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载排行榜数据时出错: {e}")
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
            print(f"保存排行榜数据时出错: {e}")

    def _backup_rankings(self):
        try:
            with open(self.rank_backup_file, 'w', encoding='utf-8') as f:
                json.dump(self.rankings, f, ensure_ascii=False, indent=2)
            print(f"排行榜数据已备份到 {self.rank_backup_file}")
        except Exception as e:
            print(f"备份排行榜数据时出错: {e}")

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

    def _init_board(self) -> np.ndarray:
        return np.zeros((self.board_size, self.board_size), dtype=int)

    def _is_valid_move(self, board: np.ndarray, x: int, y: int) -> bool:
        return 0 <= x < self.board_size and 0 <= y < self.board_size and board[x, y] == 0

    def _check_win(self, board: np.ndarray, x: int, y: int, player: int) -> bool:
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]
        for dx, dy in directions:
            count = 1
            for i in range(1, 5):
                nx, ny = x + dx * i, y + dy * i
                if 0 <= nx < self.board_size and 0 <= ny < self.board_size and board[nx, ny] == player:
                    count += 1
                else:
                    break
            for i in range(1, 5):
                nx, ny = x - dx * i, y - dy * i
                if 0 <= nx < self.board_size and 0 <= ny < self.board_size and board[nx, ny] == player:
                    count += 1
                else:
                    break
            if count >= 5:
                return True
        return False

    def _check_draw(self, board: np.ndarray) -> bool:
        return np.all(board != 0)

    def _draw_board(self, board: np.ndarray, last_move: Optional[Tuple[int, int]] = None, session_id: str = "default") -> str:
        cell_size = 40
        margin = 40
        size = self.board_size * cell_size + 2 * margin
        image = PILImage.new("RGB", (size, size), (220, 220, 220))
        draw = ImageDraw.Draw(image)
        font = self._get_system_font(20)

        board_end = margin + (self.board_size - 1) * cell_size

        for i in range(self.board_size):
            x = margin + i * cell_size
            draw.line((x, margin, x, board_end), fill="black")
            draw.line((margin, x, board_end, x), fill="black")

        star_points = [(3, 3), (11, 3), (3, 11), (11, 11), (7, 7)]
        for sx, sy in star_points:
            cx, cy = margin + sx * cell_size, margin + sy * cell_size
            draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill="black")

        for i in range(self.board_size):
            for j in range(self.board_size):
                if board[i, j] != 0:
                    cx, cy = margin + i * cell_size, margin + j * cell_size
                    color = "black" if board[i, j] == 1 else "white"
                    draw.ellipse((cx - 15, cy - 15, cx + 15, cy + 15), fill=color)
                    if last_move and last_move == (i, j):
                        mark_color = "red"
                        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=mark_color)

        for i in range(self.board_size):
            col_label = chr(65 + i)
            row_label = str(i + 1)
            draw.text((margin - 30, margin + i * cell_size), col_label, fill="black", font=font, anchor="mm")
            draw.text((board_end + 30, margin + i * cell_size), col_label, fill="black", font=font, anchor="mm")
            draw.text((margin + i * cell_size, margin - 30), row_label, fill="black", font=font, anchor="mm")
            draw.text((margin + i * cell_size, board_end + 30), row_label, fill="black", font=font, anchor="mm")

        image_path = str(self.data_path / f"board_{session_id}.png")
        image.save(image_path)
        return image_path

    def _get_system_font(self, size: int) -> ImageFont:
        try:
            if self.font_path.exists():
                return ImageFont.truetype(str(self.font_path), size)
            if platform.system() == "Windows":
                font_path = "C:/Windows/Fonts/simhei.ttf"
            elif platform.system() == "Darwin":
                font_path = "/System/Library/Fonts/PingFang.ttc"
            else:
                font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            return ImageFont.truetype(font_path, size)
        except Exception as e:
            print(f"无法加载系统字体: {e}")
            try:
                return ImageFont.truetype("arial.ttf", size)
            except:
                return ImageFont.load_default()

    def _draw_rankings_image(self, session_id: str = "default") -> str:
        sorted_rankings = sorted(
            self.rankings.items(),
            key=lambda x: x[1]["wins"],
            reverse=True
        )
        if not sorted_rankings:
            return ""

        sorted_rankings = sorted_rankings[:10]

        title_height = 50
        cell_height = 40
        cell_widths = [60, 150, 80, 80, 80, 100, 100]
        total_width = sum(cell_widths)
        total_height = title_height + cell_height * (len(sorted_rankings) + 1)
        margin = 20
        image = PILImage.new("RGB", (total_width + margin * 2, total_height + margin * 2), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        font = self._get_system_font(20)
        title_font = self._get_system_font(24)

        title = "五子棋排行榜（按胜场数排序）"
        draw.text((total_width // 2 + margin, margin + title_height // 2), title, fill="black", font=title_font, anchor="mm")

        headers = ["排名", "玩家名", "胜场", "平局", "负场", "总对局", "胜率"]
        x_pos = margin
        y_pos = margin + title_height
        for i, header in enumerate(headers):
            draw.text((x_pos + cell_widths[i] // 2, y_pos + cell_height // 2), header, fill="black", font=font, anchor="mm")
            x_pos += cell_widths[i]

        draw.line((margin, margin + title_height + cell_height, total_width + margin, margin + title_height + cell_height), fill="black", width=2)
        for i in range(len(cell_widths) + 1):
            x = margin + sum(cell_widths[:i])
            draw.line((x, margin + title_height, x, total_height + margin), fill="black", width=1)
        for i in range(len(sorted_rankings) + 2):
            y = margin + title_height + cell_height * i
            draw.line((margin, y, total_width + margin, y), fill="black", width=1)

        y_pos = margin + title_height + cell_height
        for i, (player_id, data) in enumerate(sorted_rankings, 1):
            wins = data["wins"]
            losses = data["losses"]
            draws = data["draws"]
            total_games = wins + losses + draws
            win_rate = (wins / total_games * 100) if total_games > 0 else 0
            row_data = [str(i), data['name'], str(wins), str(draws), str(losses), str(total_games), f"{win_rate:.2f}%"]
            x_pos = margin
            for j, text in enumerate(row_data):
                draw.text((x_pos + cell_widths[j] // 2, y_pos + cell_height // 2), text, fill="black", font=font, anchor="mm")
                x_pos += cell_widths[j]
            y_pos += cell_height

        image_path = str(self.data_path / f"rankings_{session_id}.png")
        image.save(image_path)
        return image_path

    def _parse_position(self, text: str) -> Optional[Tuple[int, int]]:
        text = text.strip().upper()
        match = re.match(r'^[A-O](1[0-5]|[1-9])$', text, re.IGNORECASE)
        if match:
            letter = text[0].upper()
            number = text[1:]
            col = ord(letter) - ord('A')
            row = int(number) - 1
            return (row, col)
        return None

    def _is_game_started(self, game: dict) -> bool:
        return game["players"][2] is not None

    def _is_game_player(self, game: dict, sender_id: str) -> bool:
        return sender_id in [game["players"][1]["id"], game["players"][2]["id"] if game["players"][2] else None]

    def _count_line(self, board: np.ndarray, x: int, y: int, dx: int, dy: int, player: int) -> Tuple[int, bool, bool, int, int]:
        count = 1
        left_open = False
        right_open = False
        left_spaces = 0
        right_spaces = 0

        for i in range(1, 5):
            nx, ny = x + dx * i, y + dy * i
            if 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                if board[nx, ny] == player:
                    count += 1
                elif board[nx, ny] == 0:
                    right_open = True
                    right_spaces += 1
                    for j in range(i + 1, 5):
                        nnx, nny = x + dx * j, y + dy * j
                        if 0 <= nnx < self.board_size and 0 <= nny < self.board_size and board[nnx, nny] == 0:
                            right_spaces += 1
                        else:
                            break
                    break
                else:
                    break
            else:
                break

        for i in range(1, 5):
            nx, ny = x - dx * i, y - dy * i
            if 0 <= nx < self.board_size and 0 <= ny < self.board_size:
                if board[nx, ny] == player:
                    count += 1
                elif board[nx, ny] == 0:
                    left_open = True
                    left_spaces += 1
                    for j in range(i + 1, 5):
                        nnx, nny = x - dx * j, y - dy * j
                        if 0 <= nnx < self.board_size and 0 <= nny < self.board_size and board[nnx, nny] == 0:
                            left_spaces += 1
                        else:
                            break
                    break
                else:
                    break
            else:
                break

        return count, left_open, right_open, left_spaces, right_spaces

    def _evaluate_position(self, board: np.ndarray, x: int, y: int, player: int, opponent: int) -> int:
        score = 0
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]
        temp_board = board.copy()
        temp_board[x, y] = player

        own_shapes = []
        for dx, dy in directions:
            count, left_open, right_open, left_spaces, right_spaces = self._count_line(temp_board, x, y, dx, dy, player)
            if count >= 5:
                return 1000000
            elif count == 4:
                if left_open and right_open and (left_spaces + right_spaces + count >= 5):
                    return 500000
                elif left_open or right_open:
                    score += 100000
            elif count == 3:
                if left_open and right_open and (left_spaces + right_spaces + count >= 5):
                    score += 50000
                    own_shapes.append((3, True))
                elif (left_open and left_spaces >= 2) or (right_open and right_spaces >= 2):
                    score += 5000
                elif left_open or right_open:
                    score += 500
            elif count == 2:
                if left_open and right_open and (left_spaces + right_spaces + count >= 5):
                    score += 2000
                    own_shapes.append((2, True))
                elif left_open or right_open:
                    score += 200

        if sum(1 for shape in own_shapes if shape == (3, True)) >= 2:
            score += 200000
        if sum(1 for shape in own_shapes if shape == (2, True)) >= 2:
            score += 10000

        temp_board[x, y] = opponent
        opp_shapes = []
        for dx, dy in directions:
            count, left_open, right_open, left_spaces, right_spaces = self._count_line(temp_board, x, y, dx, dy, opponent)
            if count >= 5:
                return 900000
            elif count == 4:
                if left_open and right_open and (left_spaces + right_spaces + count >= 5):
                    score += 400000
                elif left_open or right_open:
                    score += 80000
            elif count == 3:
                if left_open and right_open and (left_spaces + right_spaces + count >= 5):
                    score += 40000
                    opp_shapes.append((3, True))
                elif (left_open and left_spaces >= 2) or (right_open and right_spaces >= 2):
                    score += 4000
                elif left_open or right_open:
                    score += 400
            elif count == 2:
                if left_open and right_open and (left_spaces + right_spaces + count >= 5):
                    score += 1500
                    opp_shapes.append((2, True))
                elif left_open or right_open:
                    score += 150

        if sum(1 for shape in opp_shapes if shape == (3, True)) >= 2:
            score += 150000
        if sum(1 for shape in opp_shapes if shape == (2, True)) >= 2:
            score += 5000

        center_dist = abs(x - self.board_size // 2) + abs(y - self.board_size // 2)
        score += (self.board_size * 2 - center_dist) * 10

        if score < 1000:
            temp_board[x, y] = 0
            for dx, dy in directions:
                for i in range(-4, 5):
                    nx, ny = x + dx * i, y + dy * i
                    if 0 <= nx < self.board_size and 0 <= ny < self.board_size and board[nx, ny] == opponent:
                        temp_board[nx, ny] = 0
                        opp_future_score = 0
                        count, left_open, right_open, left_spaces, right_spaces = self._count_line(temp_board, nx, ny, dx, dy, opponent)
                        if count >= 4 and (left_open or right_open):
                            opp_future_score += 30000
                        elif count == 3 and left_open and right_open:
                            opp_future_score += 10000
                        temp_board[nx, ny] = opponent
                        score += opp_future_score * 0.5

        return score

    def _ai_move(self, session_id: str) -> Optional[Tuple[int, int]]:
        if session_id not in self.games:
            return None

        game = self.games[session_id]
        board = game["board"]
        current_player = game["current_player"]
        opponent = 3 - current_player

        best_move = None
        best_score = -1
        valid_moves = []

        for i in range(self.board_size):
            for j in range(self.board_size):
                if board[i, j] == 0:
                    score = self._evaluate_position(board, i, j, current_player, opponent)
                    valid_moves.append((i, j, score))
                    if score > best_score:
                        best_score = score
                        best_move = (i, j)

        if best_move is None and valid_moves:
            best_move = random.choice(valid_moves)[:2]

        return best_move

    async def _handle_move(self, event: AstrMessageEvent, position: str, send_immediate: bool = True):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏，请先使用 '/五子棋' 命令开始游戏。")
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            yield event.plain_result("游戏尚未开始，请等待其他玩家加入或选择 '/人机对战'。")
            return

        sender_id = event.get_sender_id()
        current_player = game["current_player"]
        player_data = game["players"][current_player]

        if player_data is None:
            yield event.plain_result("游戏尚未有足够的玩家，请等待其他玩家加入。")
            return
        if sender_id not in [game["players"][1]["id"], game["players"][2]["id"] if game["players"][2] else None]:
            return
        if player_data["id"] != sender_id:
            yield event.plain_result(f"当前轮到 {player_data['name']} ({'黑棋' if current_player == 1 else '白棋'}) 落子，请等待您的回合。")
            return

        if len(position) < 2:
            yield event.plain_result("落子格式错误，请使用类似 'H7' 或 '落子 H7' 的格式。")
            return

        try:
            col = ord(position[0].upper()) - ord('A')
            row = int(position[1:]) - 1
            if not self._is_valid_move(game["board"], row, col):
                yield event.plain_result("无效的落子位置，请检查位置是否在棋盘范围内且未被占用。")
                return

            if "history" not in game:
                game["history"] = []
            game["history"].append({
                "player": current_player,
                "position": position.upper(),
                "move": len(game["history"]) + 1
            })

            game["board"][row, col] = current_player
            game["last_move"] = (row, col)

            board_path = self._draw_board(game["board"], game["last_move"], session_id)
            player_name = "黑棋" if current_player == 1 else "白棋"
            human_move_message = f"{player_name} 落子于 {position.upper()}。"

            if send_immediate:
                chain = [
                    Plain(human_move_message),
                    Image.fromFileSystem(board_path)
                ]
                yield event.chain_result(chain)

            if self._check_win(game["board"], row, col, current_player):
                winner = player_data
                loser = game["players"][3 - current_player]
                yield event.plain_result(f"游戏结束！{winner['name']} ({player_name}) 获胜！\n输家：{loser['name']} ({'白棋' if current_player == 1 else '黑棋'})")
                self._update_rankings(winner['id'], winner['name'], loser['id'], loser['name'])
                del self.games[session_id]
                return
            elif self._check_draw(game["board"]):
                black_player = game["players"][1]
                white_player = game["players"][2]
                yield event.plain_result(f"游戏结束！棋盘已满，双方平局！\n黑棋：{black_player['name']}\n白棋：{white_player['name']}")
                self._update_draw_rankings(black_player['id'], black_player['name'], white_player['id'], white_player['name'])
                del self.games[session_id]
                return

            game["current_player"] = 3 - current_player

            if game["players"][game["current_player"]] and game["players"][game["current_player"]].get("is_ai", False):
                await asyncio.sleep(1)
                ai_move = self._ai_move(session_id)
                if ai_move:
                    ai_row, ai_col = ai_move
                    game["board"][ai_row, ai_col] = game["current_player"]
                    game["last_move"] = (ai_row, ai_col)
                    ai_player_name = "黑棋" if game["current_player"] == 1 else "白棋"
                    ai_position = f"{chr(65 + ai_col)}{ai_row + 1}"
                    game["history"].append({
                        "player": game["current_player"],
                        "position": ai_position,
                        "move": len(game["history"]) + 1
                    })
                    board_path = self._draw_board(game["board"], game["last_move"], session_id)
                    chain = [
                        Plain(f"{human_move_message}\n{ai_player_name} 落子于 {ai_position}。"),
                        Image.fromFileSystem(board_path)
                    ]
                    yield event.chain_result(chain)

                    if self._check_win(game["board"], ai_row, ai_col, game["current_player"]):
                        winner = game["players"][game["current_player"]]
                        loser = game["players"][3 - game["current_player"]]
                        yield event.plain_result(f"游戏结束！{winner['name']} ({ai_player_name}) 获胜！\n输家：{loser['name']} ({'白棋' if game['current_player'] == 1 else '黑棋'})")
                        self._update_rankings(winner['id'], winner['name'], loser['id'], loser['name'])
                        del self.games[session_id]
                        return
                    elif self._check_draw(game["board"]):
                        black_player = game["players"][1]
                        white_player = game["players"][2]
                        yield event.plain_result(f"游戏结束！棋盘已满，双方平局！\n黑棋：{black_player['name']}\n白棋：{white_player['name']}")
                        self._update_draw_rankings(black_player['id'], black_player['name'], white_player['id'], white_player['name'])
                        del self.games[session_id]
                        return

                    game["current_player"] = 3 - game["current_player"]
        except (ValueError, IndexError):
            yield event.plain_result("落子格式错误，请使用类似 'H7' 或 '落子 H7' 的格式。")

    @filter.command("五子棋")
    async def start_game(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id in self.games:
            yield event.plain_result("当前群组已有一局五子棋游戏正在进行，请先结束当前游戏。")
            return

        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        self.games[session_id] = {
            "board": self._init_board(),
            "current_player": 1,
            "last_move": None,
            "players": {1: {"id": sender_id, "name": sender_name}, 2: None},
            "history": []
        }
        self.undo_stats[session_id] = {}
        yield event.plain_result(f"五子棋游戏开始！{sender_name} 为黑棋（先手）。\n其他玩家可使用 '/加入五子棋' 加入游戏成为白棋。\n等待加入时间为 {self.join_timeout} 秒。\n发起者可使用 '/取消五子棋' 取消游戏，或使用 '/人机对战' 与 AI 对战。")

        task = asyncio.create_task(self._wait_for_join_timeout(session_id, event.unified_msg_origin))
        self.wait_tasks[session_id] = task

    async def _wait_for_join_timeout(self, session_id: str, unified_msg_origin):
        await asyncio.sleep(self.join_timeout)
        if session_id in self.games and self.games[session_id]["players"][2] is None:
            await self.context.send_message(unified_msg_origin, f"等待玩家加入超时，未有白棋玩家加入，游戏结束。")
            if session_id in self.games:
                del self.games[session_id]
            if session_id in self.wait_tasks:
                del self.wait_tasks[session_id]

    @filter.command("人机对战")
    async def start_ai_game(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏，请先使用 '/五子棋' 命令开始游戏。")
            return

        game = self.games[session_id]
        sender_id = event.get_sender_id()
        if game["players"][1]["id"] != sender_id:
            yield event.plain_result("只有游戏发起者可以选择与 AI 对战。")
            return
        if game["players"][2] is not None:
            yield event.plain_result("游戏已经开始，无法选择与 AI 对战。")
            return

        game["players"][2] = {"id": "AI", "name": "AI 玩家", "is_ai": True}
        if session_id in self.wait_tasks:
            task = self.wait_tasks[session_id]
            task.cancel()
            del self.wait_tasks[session_id]

        yield event.plain_result(f"已选择与 AI 对战！游戏正式开始，轮到黑棋落子。")
        board_path = self._draw_board(game["board"], session_id=session_id)
        yield event.image_result(board_path)

    @filter.command("取消五子棋")
    async def cancel_game(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏。")
            return

        game = self.games[session_id]
        sender_id = event.get_sender_id()
        if game["players"][1]["id"] != sender_id:
            yield event.plain_result("只有游戏发起者可以取消游戏。")
            return
        if game["players"][2] is not None:
            yield event.plain_result("游戏已经开始，无法取消。请使用 '/结束下棋' 结束游戏。")
            return

        if session_id in self.wait_tasks:
            task = self.wait_tasks[session_id]
            task.cancel()
            del self.wait_tasks[session_id]

        del self.games[session_id]
        yield event.plain_result("五子棋游戏已取消。")

    @filter.regex(r'^(加入五子棋|join gomoku)$', flags=re.IGNORECASE, priority=1)
    async def join_game(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏，请先使用 '/五子棋' 命令开始游戏。")
            return

        game = self.games[session_id]
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()

        if game["players"][2] is not None:
            yield event.plain_result("游戏已有两位玩家，无法加入。")
            return
        if game["players"][1]["id"] == sender_id:
            yield event.plain_result("您已经是黑棋玩家，无法再次加入。")
            return

        game["players"][2] = {"id": sender_id, "name": sender_name}
        if session_id in self.wait_tasks:
            task = self.wait_tasks[session_id]
            task.cancel()
            del self.wait_tasks[session_id]

        yield event.plain_result(f"{sender_name} 加入游戏，成为白棋玩家！游戏正式开始，轮到黑棋落子。")
        board_path = self._draw_board(game["board"], session_id=session_id)
        yield event.image_result(board_path)

    @filter.command("落子")
    async def make_move(self, event: AstrMessageEvent, position: str):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏，请先使用 '/五子棋' 命令开始游戏。")
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            yield event.plain_result("游戏尚未开始，请等待其他玩家加入或选择 '/人机对战'。")
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        if session_id in self.games and game["players"][2] and game["players"][2].get("is_ai", False):
            async for result in self._handle_move(event, position, send_immediate=False):
                yield result
        else:
            async for result in self._handle_move(event, position, send_immediate=True):
                yield result

    @filter.regex(r'^[A-Oa-o](1[0-5]|[1-9])$', flags=re.IGNORECASE)
    async def handle_coordinate_move(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        message_text = event.message_str.strip()
        pos = self._parse_position(message_text)
        if pos:
            if session_id in self.games and game["players"][2] and game["players"][2].get("is_ai", False):
                async for result in self._handle_move(event, message_text, send_immediate=False):
                    yield result
            else:
                async for result in self._handle_move(event, message_text, send_immediate=True):
                    yield result

    @filter.regex(r'^(悔棋|undo)$', flags=re.IGNORECASE, priority=1)
    async def handle_undo_request(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        current_player = None
        if game["players"][1]["id"] == sender_id:
            current_player = 1
        elif game["players"][2]["id"] == sender_id:
            current_player = 2

        if current_player is None:
            return

        if "history" not in game or not game["history"]:
            yield event.plain_result("还没有落子，无法悔棋。")
            return

        if session_id in self.undo_requests:
            yield event.plain_result("已经有一个悔棋请求正在等待回应，请等待对方回应。")
            return

        history = game["history"]
        found_player_move = False
        moves_to_undo = 0
        for i in range(len(history) - 1, max(-1, len(history) - 3), -1):
            if i >= 0 and history[i]["player"] == current_player:
                found_player_move = True
                moves_to_undo = len(history) - i
                break

        if not found_player_move:
            yield event.plain_result("最近两步内没有您的棋，无法申请悔棋。")
            return

        if session_id not in self.undo_stats:
            self.undo_stats[session_id] = {}

        if sender_id not in self.undo_stats[session_id]:
            self.undo_stats[session_id][sender_id] = {
                "success_count": 0,
                "request_count": 0,
                "last_move": 0
            }

        stats = self.undo_stats[session_id][sender_id]
        if stats["success_count"] >= 1:
            yield event.plain_result("您在本局游戏中已经成功悔棋一次，无法再次申请悔棋。")
            return

        if stats["request_count"] >= 3:
            yield event.plain_result("您在本局游戏中已经申请悔棋 3 次，无法再次申请悔棋。")
            return

        if stats["last_move"] == len(history):
            yield event.plain_result("您在本次落子期间已经申请过悔棋，无法再次申请，请等待下一次落子。")
            return

        stats["request_count"] += 1
        stats["last_move"] = len(history)

        opponent_player = 3 - current_player
        opponent_data = game["players"][opponent_player]
        proposer_name = game["players"][current_player]["name"]
        if opponent_data.get("is_ai", False):
            if moves_to_undo == 1:
                last_move = history.pop()
                last_pos = self._parse_position(last_move["position"])
                if last_pos:
                    row, col = last_pos
                    game["board"][row, col] = 0
            elif moves_to_undo == 2:
                last_move = history.pop()
                last_pos = self._parse_position(last_move["position"])
                if last_pos:
                    row, col = last_pos
                    game["board"][row, col] = 0
                second_last_move = history.pop()
                second_last_pos = self._parse_position(second_last_move["position"])
                if second_last_pos:
                    row, col = second_last_pos
                    game["board"][row, col] = 0

            game["last_move"] = None
            game["current_player"] = current_player
            board_path = self._draw_board(game["board"], session_id=session_id)
            player_name = "黑棋" if current_player == 1 else "白棋"
            chain = [
                Plain(f"悔棋成功，AI 已同意，撤销了{moves_to_undo}步棋，当前轮到 {player_name} 落子。"),
                Image.fromFileSystem(board_path)
            ]
            yield event.chain_result(chain)
        else:
            opponent_name = opponent_data["name"]
            self.undo_requests[session_id] = {
                "proposer": sender_id,
                "moves_to_undo": moves_to_undo,
                "timeout_task": asyncio.create_task(self._undo_request_timeout(session_id, event.unified_msg_origin))
            }
            yield event.plain_result(f"{proposer_name} 提出悔棋请求！\n{opponent_name}，请在 30 秒内回复 '接受悔棋' 或 '拒绝悔棋' 以同意或拒绝悔棋请求。")

    async def _undo_request_timeout(self, session_id: str, unified_msg_origin):
        await asyncio.sleep(30)
        if session_id in self.undo_requests:
            del self.undo_requests[session_id]
            await self.context.send_message(unified_msg_origin, "悔棋请求超时，游戏继续。")

    @filter.regex(r'^(接受悔棋|accept undo)$', flags=re.IGNORECASE, priority=1)
    async def handle_accept_undo(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        if session_id not in self.undo_requests:
            return

        opponent_player = None
        if game["players"][1]["id"] == sender_id:
            opponent_player = 2
        elif game["players"][2]["id"] == sender_id:
            opponent_player = 1

        if opponent_player is None or game["players"][opponent_player]["id"] != self.undo_requests[session_id]["proposer"]:
            return

        if session_id in self.undo_requests:
            proposer_id = self.undo_requests[session_id]["proposer"]
            moves_to_undo = self.undo_requests[session_id]["moves_to_undo"]
            del self.undo_requests[session_id]

        if session_id in self.undo_stats and proposer_id in self.undo_stats[session_id]:
            self.undo_stats[session_id][proposer_id]["success_count"] += 1

        history = game["history"]
        if moves_to_undo == 1 and len(history) >= 1:
            last_move = history.pop()
            last_pos = self._parse_position(last_move["position"])
            if last_pos:
                row, col = last_pos
                game["board"][row, col] = 0
        elif moves_to_undo == 2 and len(history) >= 2:
            last_move = history.pop()
            last_pos = self._parse_position(last_move["position"])
            if last_pos:
                row, col = last_pos
                game["board"][row, col] = 0
            second_last_move = history.pop()
            second_last_pos = self._parse_position(second_last_move["position"])
            if second_last_pos:
                row, col = second_last_pos
                game["board"][row, col] = 0
        else:
            yield event.plain_result("历史记录不足，无法完成悔棋，但已接受请求。")
            return

        game["last_move"] = None
        game["current_player"] = game["players"][opponent_player]["id"] == proposer_id and opponent_player or (3 - opponent_player)
        board_path = self._draw_board(game["board"], session_id=session_id)
        player_name = "黑棋" if game["current_player"] == 1 else "白棋"
        proposer_name = game["players"][game["current_player"]]["name"]
        chain = [
            Plain(f"悔棋请求被接受！撤销了{moves_to_undo}步棋，当前轮到 {player_name} ({proposer_name}) 落子。"),
            Image.fromFileSystem(board_path)
        ]
        yield event.chain_result(chain)

    @filter.regex(r'^(拒绝悔棋|reject undo)$', flags=re.IGNORECASE, priority=1)
    async def handle_reject_undo(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        if session_id not in self.undo_requests:
            return

        opponent_player = None
        if game["players"][1]["id"] == sender_id:
            opponent_player = 2
        elif game["players"][2]["id"] == sender_id:
            opponent_player = 1

        if opponent_player is None or game["players"][opponent_player]["id"] != self.undo_requests[session_id]["proposer"]:
            return

        if session_id in self.undo_requests:
            del self.undo_requests[session_id]

        yield event.plain_result("悔棋请求被拒绝，游戏继续。")

    @filter.command("查看棋局")
    async def view_board(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏。")
            return

        game = self.games[session_id]
        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        board_path = self._draw_board(game["board"], game["last_move"], session_id)
        current_player = game["current_player"]
        player_data = game["players"][current_player]
        player_name = player_data["name"] if player_data else "未加入"
        yield event.plain_result(f"当前轮到 {'黑棋' if current_player == 1 else '白棋'} ({player_name}) 落子。")
        yield event.image_result(board_path)

    @filter.regex(r'^(认输|surrender)$', flags=re.IGNORECASE, priority=1)
    async def handle_surrender(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        current_player = None
        if game["players"][1] and game["players"][1]["id"] == sender_id:
            current_player = 1
        elif game["players"][2] and game["players"][2]["id"] == sender_id:
            current_player = 2

        if current_player is None:
            return

        player_name = "黑棋" if current_player == 1 else "白棋"
        loser = game["players"][current_player]
        winner = game["players"][3 - current_player]
        yield event.plain_result(f"{loser['name']} ({player_name}) 认输！游戏结束！\n胜者：{winner['name']} ({'白棋' if current_player == 1 else '黑棋'})")
        self._update_rankings(winner['id'], winner['name'], loser['id'], loser['name'])
        del self.games[session_id]

    @filter.regex(r'^(求和|draw|peace)$', flags=re.IGNORECASE, priority=1)
    async def handle_peace_request(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        current_player = None
        if game["players"][1] and game["players"][1]["id"] == sender_id:
            current_player = 1
        elif game["players"][2] and game["players"][2]["id"] == sender_id:
            current_player = 2

        if current_player is None:
            return

        if session_id in self.peace_requests:
            yield event.plain_result("已经有一个求和请求正在等待回应，请等待对方回应。")
            return

        proposer_name = game["players"][current_player]["name"]
        opponent_player = 3 - current_player
        opponent_data = game["players"][opponent_player]
        if opponent_data is None:
            yield event.plain_result("对方未加入，无法提出求和。")
            return
        if opponent_data.get("is_ai", False):
            accept = random.choice([True, False])
            if accept:
                black_player = game["players"][1]["name"]
                white_player = game["players"][2]["name"]
                yield event.plain_result(f"{proposer_name} 提出求和！\nAI 玩家已接受求和请求！游戏结束，双方平局！\n黑棋：{black_player}\n白棋：{white_player}")
                self._update_draw_rankings(game["players"][1]["id"], black_player, game["players"][2]["id"], white_player)
                del self.games[session_id]
            else:
                yield event.plain_result(f"{proposer_name} 提出求和！\nAI 玩家已拒绝求和请求，游戏继续。")
            return

        opponent_name = opponent_data["name"]
        self.peace_requests[session_id] = {
            "proposer": sender_id,
            "timeout_task": asyncio.create_task(self._peace_request_timeout(session_id, event.unified_msg_origin))
        }
        yield event.plain_result(f"{proposer_name} 提出求和！\n{opponent_name}，请在 30 秒内回复 '接受求和' 或 '拒绝求和' 以同意或拒绝求和请求。")

    async def _peace_request_timeout(self, session_id: str, unified_msg_origin):
        await asyncio.sleep(30)
        if session_id in self.peace_requests:
            del self.peace_requests[session_id]
            await self.context.send_message(unified_msg_origin, "求和请求超时，游戏继续。")

    @filter.regex(r'^(接受求和|accept draw|accept peace)$', flags=re.IGNORECASE, priority=1)
    async def handle_accept_peace(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        if session_id not in self.peace_requests:
            return

        opponent_player = None
        if game["players"][1] and game["players"][1]["id"] == sender_id:
            opponent_player = 2
        elif game["players"][2] and game["players"][2]["id"] == sender_id:
            opponent_player = 1

        if opponent_player is None or game["players"][opponent_player]["id"] != self.peace_requests[session_id]["proposer"]:
            return

        if session_id in self.peace_requests:
            del self.peace_requests[session_id]

        black_player = game["players"][1]
        white_player = game["players"][2]
        yield event.plain_result(f"求和请求被接受！游戏结束，双方平局！\n黑棋：{black_player['name']}\n白棋：{white_player['name']}")
        self._update_draw_rankings(black_player['id'], black_player['name'], white_player['id'], white_player['name'])
        del self.games[session_id]

    @filter.regex(r'^(拒绝求和|reject draw|reject peace)$', flags=re.IGNORECASE, priority=1)
    async def handle_reject_peace(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            return

        game = self.games[session_id]
        if not self._is_game_started(game):
            return

        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        if session_id not in self.peace_requests:
            return

        opponent_player = None
        if game["players"][1] and game["players"][1]["id"] == sender_id:
            opponent_player = 2
        elif game["players"][2] and game["players"][2]["id"] == sender_id:
            opponent_player = 1

        if opponent_player is None or game["players"][opponent_player]["id"] != self.peace_requests[session_id]["proposer"]:
            return

        if session_id in self.peace_requests:
            del self.peace_requests[session_id]

        yield event.plain_result("求和请求被拒绝，游戏继续。")

    @filter.command("结束下棋")
    async def end_game(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏。")
            return

        game = self.games[session_id]
        sender_id = event.get_sender_id()
        if not self._is_game_player(game, sender_id):
            return

        del self.games[session_id]
        yield event.plain_result("五子棋游戏已结束。")

    @filter.command("强制结束游戏")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def force_end_game(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self.games:
            yield event.plain_result("当前群组没有正在进行的五子棋游戏。")
            return

        del self.games[session_id]
        yield event.plain_result("管理员已强制结束五子棋游戏。")

    @filter.command("五子棋帮助")
    async def show_help(self, event: AstrMessageEvent):
        help_text = (
            "🎲 五子棋游戏帮助 🎲\n\n"
            "五子棋是一款经典的两人对弈游戏，目标是在 15x15 的棋盘上率先连成五子即可获胜。以下是游戏功能和指令介绍：\n\n"
            "【游戏指令】\n"
            "- /五子棋：开始一局新的五子棋游戏，黑棋先手。\n"
            "- /加入五子棋 或 join gomoku：加入当前游戏，成为白棋玩家。\n"
            "- /人机对战：与 AI 对战，AI 作为白棋玩家。\n"
            "- /取消五子棋：游戏未开始时，发起者可取消游戏。\n"
            "- 落子 <坐标> 或 直接输入坐标（如 H7）：在指定位置落子，坐标格式为字母+数字（如 H7）。\n"
            "- /查看棋局：查看当前棋盘状态。\n"
            "- /悔棋 或 undo：申请悔棋，撤销上一步（或两步，若对手已下棋），每局限成功一次，最多申请三次。\n"
            "- /接受悔棋 或 accept undo：接受对方的悔棋请求。\n"
            "- /拒绝悔棋 或 reject undo：拒绝对方的悔棋请求。\n"
            "- /求和 或 draw/peace：提出平局请求。\n"
            "- /接受求和 或 accept draw/peace：接受对方的平局请求。\n"
            "- /拒绝求和 或 reject draw/peace：拒绝对方的平局请求。\n"
            "- /认输 或 surrender：主动认输，结束游戏。\n"
            "- /结束下棋：结束当前游戏。\n"
            "- /强制结束游戏：管理员专用，强制结束当前游戏。\n\n"
            "【战绩查询】\n"
            "- /五子棋排行榜：查看五子棋游戏排行榜（按胜场数排序）。\n"
            "- /我的战绩：查看个人五子棋战绩。\n\n"
            "【游戏规则】\n"
            "- 棋盘为 15x15，黑棋先手，双方轮流落子。\n"
            "- 率先在水平、垂直或斜线方向连成五子者获胜。\n"
            "- 若棋盘填满仍无胜负，则为平局。\n\n"
            "如有疑问或建议，请联系开发者。祝您游戏愉快！😊"
        )
        yield event.plain_result(help_text)

    @filter.command("五子棋排行榜")
    async def show_rankings(self, event: AstrMessageEvent):
        if not self.rankings:
            yield event.plain_result("排行榜为空，暂无玩家数据。")
            return

        session_id = event.session_id
        image_path = self._draw_rankings_image(session_id)
        if image_path:
            yield event.image_result(image_path)
        else:
            yield event.plain_result("排行榜为空，暂无玩家数据。")

    @filter.command("我的战绩")
    async def show_my_stats(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        if sender_id not in self.rankings:
            yield event.plain_result("您还没有参与过五子棋游戏，暂无战绩数据。")
            return

        data = self.rankings[sender_id]
        wins = data["wins"]
        losses = data["losses"]
        draws = data["draws"]
        total_games = wins + losses + draws
        win_rate = (wins / total_games * 100) if total_games > 0 else 0

        stats_text = f"您的五子棋战绩：\n"
        stats_text += f"名称：{data['name']}\n"
        stats_text += f"胜：{wins} 局\n"
        stats_text += f"平：{draws} 局\n"
        stats_text += f"负：{losses} 局\n"
        stats_text += f"总对局：{total_games} 局\n"
        stats_text += f"胜率：{win_rate:.2f}%"

        yield event.plain_result(stats_text)

    async def terminate(self):
        for task in self.wait_tasks.values():
            task.cancel()
        self.games.clear()
        self.wait_tasks.clear()
        for req in self.peace_requests.values():
            if "timeout_task" in req:
                req["timeout_task"].cancel()
        self.peace_requests.clear()
        for req in self.undo_requests.values():
            if "timeout_task" in req:
                req["timeout_task"].cancel()
        self.undo_requests.clear()
        self.undo_stats.clear()
        self._save_rankings()
