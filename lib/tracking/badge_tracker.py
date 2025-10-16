"""
Tracking de qualité des badges BLE (séquence po1→po2→...→po0→po1)
"""
import time


class BadgeTracker:
    """Tracks badge code sequences for quality control (po1→po2→...→po0→po1)"""

    def __init__(self, addr):
        self.addr = addr
        self.last_code = None
        self.last_timestamp = 0
        self.sequence_errors = 0  # Counter for sequence gaps
        self.total_expected = 0   # Total frames expected since first seen
        self.first_seen = time.time()

    def check_sequence(self, new_code, timestamp):
        """Check sequence continuity po1→po2→...→po0→po1

        Args:
            new_code: Code reçu (ex: "po1", "po2", etc.)
            timestamp: Timestamp de réception

        Returns:
            tuple: (is_valid, gap) où is_valid=True si séquence correcte, gap=nombre de frames manquées
        """
        if self.last_code is None:
            # First frame
            self.last_code = new_code
            self.last_timestamp = timestamp
            self.total_expected = 1
            return (True, 0)

        # Calculate expected code
        last_digit = int(self.last_code[2])
        if last_digit == 9:
            expected_digit = 0
        elif last_digit == 0:
            expected_digit = 1
        else:
            expected_digit = last_digit + 1

        expected_code = f"po{expected_digit}"

        # Calculate gap
        gap = 0
        if new_code != expected_code:
            gap = self._calculate_gap(self.last_code, new_code)
            self.sequence_errors += gap

        # Update state
        self.last_code = new_code
        self.last_timestamp = timestamp
        self.total_expected += (gap + 1)  # Add gap + this frame

        return (new_code == expected_code, gap)

    def _calculate_gap(self, old_code, new_code):
        """Calculate number of missed frames between old_code and new_code

        Args:
            old_code: Code précédent
            new_code: Nouveau code

        Returns:
            int: Nombre de frames manquées
        """
        old_digit = int(old_code[2])
        new_digit = int(new_code[2])

        # Map to sequence index: po1=1, po2=2, ..., po9=9, po0=10
        old_idx = old_digit if old_digit != 0 else 10
        new_idx = new_digit if new_digit != 0 else 10

        # Calculate gap with wraparound (cycle of 10)
        if new_idx > old_idx:
            gap = new_idx - old_idx - 1
        else:
            gap = (10 - old_idx) + new_idx - 1

        return gap if gap > 0 else 0

    def get_stats(self):
        """Get tracking statistics

        Returns:
            dict: Statistiques de tracking avec runtime, frames, taux de succès
        """
        runtime = time.time() - self.first_seen
        received = self.total_expected - self.sequence_errors
        success_rate = 100.0 * received / self.total_expected if self.total_expected > 0 else 0

        return {
            'addr': self.addr,
            'runtime_sec': runtime,
            'total_expected': self.total_expected,
            'received': received,
            'missed': self.sequence_errors,
            'success_rate': success_rate,
            'last_code': self.last_code
        }
