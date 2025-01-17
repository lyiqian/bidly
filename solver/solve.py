"""Solver interfaces/Classes used by app."""
import abc
import collections
import dataclasses
import logging
import typing as T

from detector import detect
from solver import converter
import solver.pythondds_min.adapter as dds_adapter


lgr = logging

CardName = str
CardAssignment = T.List[T.Dict]
TransformedCards = T.List[T.Dict]  # see outputs of converter.IPredReader
AssignedCards = T.List[T.Dict]


# Abstract #

@dataclasses.dataclass
class Solution():
    hand: object
    hand_dict: CardAssignment
    dds_result: object


@dataclasses.dataclass
class TransformationResults:
    cards: TransformedCards
    missings: T.List[CardName]  # user can decide to assign
    fps: T.List[CardName]  # more serious


@dataclasses.dataclass
class AssignmentResults:
    cards: AssignedCards
    not_assigned: T.List[CardName]


class IPresenter(abc.ABC):

    @abc.abstractmethod
    def present(self, solution: Solution):
        pass


class BridgeSolverBase(abc.ABC):
    detection: detect.CardDetection
    presenter: IPresenter

    solution_: Solution

    def __init__(self, detection, presenter) -> None:
        self.detection = detection
        self.presenter = presenter

    @abc.abstractmethod
    def transform(self) -> TransformationResults:
        pass

    @abc.abstractmethod
    def assign(self, transformed_cards: TransformedCards) -> AssignmentResults:
        pass

    @abc.abstractmethod
    def solve(self, assigned_cards: AssignedCards):
        pass

    def present(self):
        return self.presenter.present(self.solution_)


# Impl. #
class BridgeSolver(BridgeSolverBase):
    def __init__(self, detection, presenter) -> None:
        super().__init__(detection, presenter)
        self.converter = converter.get_deal_converter(reader=converter.Yolo5Reader())

    def transform(self) -> TransformationResults:
        self.converter.read(self.detection)
        # self.converter.dedup(smart=True)  # yolo5 does not seem needing this
        missings, fps = self.converter.report_missing_and_fp()

        transformation_results = TransformationResults(
            cards=self.converter.card.to_dict("records"),
            missings=missings,
            fps=fps,
        )
        return transformation_results

    def assign(self, transformed_cards: TransformedCards) -> AssignmentResults:
        assigned_cards =  self.converter.assign(transformed_cards)

        assigned_card_names = (card['name'] for card in assigned_cards)
        not_assigned_card_names = set(converter.CARD_CLASSES) - set(assigned_card_names)

        assignment_results = AssignmentResults(
            cards=assigned_cards,
            not_assigned=list(not_assigned_card_names),
        )
        return assignment_results

    def solve(self, assigned_cards: AssignedCards):
        pbn_hand = self.converter.format_pbn(assigned_cards)

        dds_result = dds_adapter.solve_hand(pbn_hand)
        lgr.debug("Got DDS result for pbn hand: %s", pbn_hand)

        self.solution_ = Solution(
            hand=pbn_hand,
            hand_dict=self.converter.list_assigned_cards(),
            dds_result=dds_result,
        )


class StringPresenter(IPresenter):
    def present(self, solution: Solution):
        formatted_hand = dds_adapter.format_hand(solution.hand)

        formatted_dd_result = dds_adapter.format_result(solution.dds_result)
        return formatted_hand, formatted_dd_result


class MonoStringPresenter(IPresenter):
    S, H, D, C = '\u2660', '\u2661', '\u2662', '\u2663'
    SYMBOL_MAP = {
        converter.SUIT_S: S,
        converter.SUIT_H: H,
        converter.SUIT_D: D,
        converter.SUIT_C: C,
    }
    HORI, VERT = '\u2500', '\u2502'
    TL, TR, BL, BR = '\u250c', '\u2510', '\u2514', '\u2518'

    SQUARE_WIDTH = 6

    def present(self, solution: Solution):
        formatted_hand = self._format_hand(solution.hand_dict)

        formatted_dd_result = self._format_result(solution.dds_result)
        return formatted_hand, formatted_dd_result

    def _format_hand(self, hand: CardAssignment) -> str:
        """Formatted example:

              ♠-             
              ♡K52           
              ♢KJ97432       
              ♣973           

        ♠85   ┌────┐ ♠QJT9742
        ♡AQJ3 │    │ ♡98     
        ♢AQT6 │    │ ♢5      
        ♣A42  └────┘ ♣J85    

              ♠AK63          
              ♡T764          
              ♢8             
              ♣KQT6          

        Leading & trailing spaces are important to ensure nice display in center-aligned text boxes.
        See method `_align_l_r()` below."""
        assert len(hand) == 52

        # Extract cards
        suit_map = collections.defaultdict(list)  # 'northd' -> list of cards
        for card in hand:
            player = card['hand']
            color, rank = card['name'][-1], card['name'][:-1]
            suit_map[player+color].append(rank)

        # Calc widths
        e_longest = self._longest_len(suit_map, converter.HAND_E) + 1  # for symbol
        w_longest = self._longest_len(suit_map, converter.HAND_W) + 1  # for symbol
        ew_longest = max(e_longest, w_longest)
        ew_row_min_width = ew_longest*2 + self.SQUARE_WIDTH + 2  # padding

        n_longest = self._longest_len(suit_map, converter.HAND_N) + 1  # for symbol
        s_longest = self._longest_len(suit_map, converter.HAND_S) + 1  # for symbol
        ns_longest = max(n_longest, s_longest)
        ns_row_min_width = ns_longest*2 - self.SQUARE_WIDTH

        width = max(ew_row_min_width, ns_row_min_width)

        ew_suit_width = (width-self.SQUARE_WIDTH-2) // 2
        ns_suit_width = self.SQUARE_WIDTH + 1 + ew_suit_width

        # Format hand row by row
        rows = []
        rows.append(self._format_align_suit(suit_map, 'north', 's', ns_suit_width, width))
        rows.append(self._format_align_suit(suit_map, 'north', 'h', ns_suit_width, width))
        rows.append(self._format_align_suit(suit_map, 'north', 'd', ns_suit_width, width))
        rows.append(self._format_align_suit(suit_map, 'north', 'c', ns_suit_width, width))

        t_bar = self.TL+self.HORI*4+self.TR
        h_bar = self.VERT+' '*4+self.VERT
        b_bar = self.BL+self.HORI*4+self.BR

        ws = self._format_align_suit(suit_map, 'west', 's', w_longest, ew_suit_width)
        es = self._format_align_suit(suit_map, 'east', 's', ew_suit_width, ew_suit_width)
        rows.append(' '.join([ws, t_bar, es]))

        wh = self._format_align_suit(suit_map, 'west', 'h', w_longest, ew_suit_width)
        eh = self._format_align_suit(suit_map, 'east', 'h', ew_suit_width, ew_suit_width)
        rows.append(' '.join([wh, h_bar, eh]))

        wd = self._format_align_suit(suit_map, 'west', 'd', w_longest, ew_suit_width)
        ed = self._format_align_suit(suit_map, 'east', 'd', ew_suit_width, ew_suit_width)
        rows.append(' '.join([wd, h_bar, ed]))

        wc = self._format_align_suit(suit_map, 'west', 'c', w_longest, ew_suit_width)
        ec = self._format_align_suit(suit_map, 'east', 'c', ew_suit_width, ew_suit_width)
        rows.append(' '.join([wc, b_bar, ec]))

        rows.append(self._format_align_suit(suit_map, 'south', 's', ns_suit_width, width))
        rows.append(self._format_align_suit(suit_map, 'south', 'h', ns_suit_width, width))
        rows.append(self._format_align_suit(suit_map, 'south', 'd', ns_suit_width, width))
        rows.append(self._format_align_suit(suit_map, 'south', 'c', ns_suit_width, width))
        rows.append('')  # avoid newline issue

        lgr.debug("deal string width to present: %s", len(rows[0]))
        return '\n'.join(rows)

    def _format_result(self, dds_result) -> str:
        records = dds_adapter.result_to_records(dds_result)
        sorted_records = sorted(records, key=lambda d: ['N', 'S', 'W', 'E'].index(d['player']))

        rows = []
        rows.append(f"  {self.C:<2}{self.D:<2}{self.H:<2}{self.S:<2}NT")
        for rec in sorted_records:
            clvl = dds_adapter.tricks_to_level(rec['cs']) or '-'
            dlvl = dds_adapter.tricks_to_level(rec['ds']) or '-'
            hlvl = dds_adapter.tricks_to_level(rec['hs']) or '-'
            slvl = dds_adapter.tricks_to_level(rec['ss']) or '-'
            nlvl = dds_adapter.tricks_to_level(rec['ns']) or '-'
            row = f"{rec['player']} {clvl:<2}{dlvl:<2}{hlvl:<2}{slvl:<2}{nlvl:<2}"
            rows.append(row)
        rows.append('')  # avoid newline issue

        return '\n'.join(rows)

    def _longest_len(self, suit_map, player):
        player_suits = (suit for pc, suit in suit_map.items() if pc.startswith(player))
        return max(len(s) for s in player_suits)

    def _format_align_suit(self, suit_map, player, color, suit_width, total_width):
        formatted_suit = self._format_suit(suit_map, player, color)
        aligned_suit = self._align_l_r(formatted_suit, suit_width, total_width)
        return aligned_suit

    def _format_suit(self, suit_map, player, color):
        cards = suit_map[player+color]
        sorted_cards = sorted(cards, key=converter.RANKS.index)
        mono_cards = ''.join('T' if c == '10' else c for c in sorted_cards) or '-'  # void
        formatted_suit = f'{self.SYMBOL_MAP[color]}{mono_cards}'
        return formatted_suit

    def _align_l_r(self, text, self_width, total_width):
        """Align left then right, to ensure nice display for center-aligned text box."""
        left_aligned = f'{{:<{self_width}}}'.format(text)
        left_right_aligned = f'{{:>{total_width}}}'.format(left_aligned)
        return left_right_aligned


class PrintPresenter(IPresenter):  # TODO ideally have another separate `View` and this only transforms
    def present(self, solution: Solution):
        formatted_hand = dds_adapter.format_hand(solution.hand)
        print(formatted_hand)

        formatted_dd_result = dds_adapter.format_result(solution.dds_result)
        print(formatted_dd_result)

        par_result = dds_adapter.calc_par(solution.dds_result)
        print(dds_adapter.format_par(par_result))
