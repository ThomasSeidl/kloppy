import logging
from typing import List, Dict, Tuple
from enum import Enum, Flag
from collections import Counter
import numpy as np
import json

from kloppy.domain import (
    attacking_direction_from_frame,
    AttackingDirection,
    BallState,
    DatasetFlag,
    Dimension,
    Frame,
    Ground,
    Metadata,
    Orientation,
    Period,
    Player,
    Point,
    Point3D,
    Position,
    Provider,
    PitchDimensions,
    Score,
    Team,
    TrackingDataset,
    Transformer,
    build_coordinate_system,
    PlayerData,
)
from kloppy.utils import Readable, performance_logging

from . import TrackingDataSerializer

logger = logging.getLogger(__name__)


class SkillCornerTrackingSerializer(TrackingDataSerializer):
    @classmethod
    def _get_frame_data(
        cls,
        teams,
        teamdict,
        players,
        player_id_to_team_dict,
        periods,
        player_dict,
        anon_players,
        ball_id,
        referee_dict,
        frame,
    ):
        frame_period = frame["period"]

        frame_id = frame["frame"]
        frame_time = cls._timestamp_from_timestring(frame["time"])

        ball_coordinates = None
        players_data = {}

        # ball_carrier = frame["possession"].get("trackable_object")
        ball_owning_team = frame["possession"].get("group")

        if ball_owning_team == "home team":
            ball_owning_team = teams[0]
            ball_state = BallState.ALIVE
        elif ball_owning_team == "away team":
            ball_owning_team = teams[1]
            ball_state = BallState.ALIVE
        else:
            ball_owning_team = None
            ball_state = BallState.DEAD

        for frame_record in frame["data"]:
            # containing x, y, trackable_object, track_id, group_name
            x = frame_record.get("x")
            y = frame_record.get("y")

            trackable_object = frame_record.get("trackable_object", None)

            track_id = frame_record.get("track_id", None)
            group_name = frame_record.get("group_name", None)

            if trackable_object == ball_id:
                group_name = "ball"
                z = frame_record.get("z")
                if z is not None:
                    z = float(z)
                ball_coordinates = Point3D(x=float(x), y=float(y), z=z)
                continue

            elif trackable_object in referee_dict.keys():
                group_name = "referee"
                continue  # Skip Referee Coords

            if group_name is None:
                group_name = teamdict.get(
                    player_id_to_team_dict.get(trackable_object)
                )

                if group_name == "home_team":
                    player = players["HOME"][trackable_object]
                elif group_name == "away_team":
                    player = players["AWAY"][trackable_object]

            if trackable_object is None:
                player_id = str(track_id)
                if group_name == "home team":
                    if f"anon_{player_id}" not in anon_players["HOME"].keys():
                        player = cls.__create_anon_player(
                            cls, teams, frame_record
                        )
                        anon_players["HOME"][f"anon_home_{player_id}"] = player
                    else:
                        player = anon_players["HOME"][f"anon_home_{player_id}"]

                elif group_name == "away team":
                    if f"anon_{player_id}" not in anon_players["AWAY"].keys():
                        player = cls.__create_anon_player(
                            cls, teams, frame_record
                        )
                        anon_players["AWAY"][f"anon_away_{player_id}"] = player
                    else:
                        player = anon_players["AWAY"][f"anon_away_{player_id}"]

            players_data[player] = PlayerData(coordinates=Point(x, y))

        return Frame(
            frame_id=frame_id,
            timestamp=frame_time,
            ball_coordinates=ball_coordinates,
            players_data=players_data,
            period=periods[frame_period],
            ball_state=ball_state,
            ball_owning_team=ball_owning_team,
            other_data={},
        )

    @classmethod
    def _timestamp_from_timestring(cls, timestring):
        m, s = timestring.split(":")
        return 60 * float(m) + float(s)

    @classmethod
    def _set_skillcorner_attacking_directions(cls, frames, periods):
        """
        with only partial tracking data we cannot rely on a single frame to
        infer the attacking directions as a simple average of only some players
        x-coords might not reflect the attacking direction.
        """
        attacking_directions = []

        for frame in frames:
            if len(frame.players_data) > 0:
                attacking_directions.append(
                    attacking_direction_from_frame(frame)
                )
            else:
                attacking_directions.append(AttackingDirection.NOT_SET)

        frame_periods = np.array([_frame.period.id for _frame in frames])

        for period in periods.keys():
            if period in frame_periods:
                count = Counter(
                    np.array(attacking_directions)[frame_periods == period]
                )
                att_direction = count.most_common()[0][0]
                periods[period].attacking_direction = att_direction
            else:
                periods[
                    period
                ].attacking_direction = AttackingDirection.NOT_SET

    def __load_json(self, file):
        return json.load(file)

    @classmethod
    def __get_periods(cls, tracking):
        """gets the Periods contained in the tracking data"""
        periods = {}

        _periods = np.array([f["period"] for f in tracking])
        unique_periods = set(_periods)
        unique_periods = [
            period for period in unique_periods if period is not None
        ]

        for period in unique_periods:
            _frames = [
                frame
                for frame in tracking
                if frame["period"] == period and frame["time"] is not None
            ]

            periods[period] = Period(
                id=period,
                start_timestamp=cls._timestamp_from_timestring(
                    _frames[0]["time"]
                ),
                end_timestamp=cls._timestamp_from_timestring(
                    _frames[-1]["time"]
                ),
            )
        return periods

    def __create_anon_player(self, teams, frame_record):
        """
        creates a Player object for a track_id'ed player with known team membership but unknown identity.

        Args:
            frame_record (dict): dictionary containing 'x', 'y', 'track_id' and 'group_name'

        Returns:
            kloppy.domain.models.common.Player

        """
        track_id = frame_record.get("track_id", None)
        group_name = frame_record.get("group_name", None)

        if group_name == "home team":
            team = teams[0]
        elif group_name == "away team":
            team = teams[1]
        else:
            raise ValueError(
                f"anonymous player with track_id `{track_id}` does not have a specified group_name."
            )

        return Player(
            player_id=f"{team.ground}_anon_{track_id}",
            team=team,
            jersey_no=None,
            name=f"Anon_{track_id}",
            first_name="Anon",
            last_name=track_id,
            starting=None,
            position=None,
            attributes={},
        )

    @staticmethod
    def __validate_inputs(inputs: Dict[str, Readable]):
        if "metadata" not in inputs:
            raise ValueError("Please specify a value for 'metadata'")
        if "raw_data" not in inputs:
            raise ValueError("Please specify a value for 'raw_data'")

    def deserialize(
        self, inputs: Dict[str, Readable], options: Dict = None
    ) -> TrackingDataset:
        """
        Deserialize SkillCorner tracking data into a `TrackingDataset`.

        Parameters
        ----------
        inputs : dict
            input `raw_data` should point to a `Readable` object containing
            the 'json' formatted raw data. input `metadata` should point to
            the json metadata data.
        options : dict
            Options for deserialization of the TRACAB file. Possible options are:  
            `include_empty_frames` (boolean): default = False to specify whether frames without
            any players_data or the ball_coordinates should be loaded  
            `sample_rate` (float between 0 and 1) to specify the amount of frames that should be loaded  
            and `limit` (int) to specify the max number of frames that will be returned.
        Returns
        -------
        dataset : TrackingDataset
        Raises
        ------
        -

        See Also
        --------

        Examples
        --------
        >>> serializer = SkillCornerSerializer()
        >>> with open("match_data.json", "rb") as meta, \
        >>>      open("structured_data.json", "rb") as raw:
        >>>     dataset = serializer.deserialize(
        >>>         inputs={
        >>>             'metadata': meta,
        >>>             'raw_data': raw
        >>>         },
        >>>         options={
        >>>         }
        >>>     )
        """
        self.__validate_inputs(inputs)

        metadata = self.__load_json(inputs["metadata"])
        raw_data = self.__load_json(inputs["raw_data"])

        if not options:
            options = {}

        sample_rate = float(options.get("sample_rate", 1.0))
        limit = int(options.get("limit", 0))
        include_empty_frames = bool(options.get("include_empty_frames", False))

        with performance_logging("Loading metadata", logger=logger):
            periods = self.__get_periods(raw_data)

            teamdict = {
                metadata["home_team"].get("id"): "home_team",
                metadata["away_team"].get("id"): "away_team",
            }

            player_id_to_team_dict = {
                player["trackable_object"]: player["team_id"]
                for player in metadata["players"]
            }

            player_dict = {
                player["trackable_object"]: player
                for player in metadata["players"]
            }

            referee_dict = {
                ref["trackable_object"]: "referee"
                for ref in metadata["referees"]
            }
            ball_id = metadata["ball"]["trackable_object"]

            # there are different pitch_sizes in SkillCorner
            pitch_size_width = metadata["pitch_width"]
            pitch_size_length = metadata["pitch_length"]

            from_coordinate_system = build_coordinate_system(
                Provider.SKILLCORNER,
                length=pitch_size_length,
                width=pitch_size_width,
            )

            to_coordinate_system = build_coordinate_system(
                options.get("coordinate_system", Provider.KLOPPY),
                length=pitch_size_length,
                width=pitch_size_width,
            )

            transformer = Transformer(
                from_coordinate_system=from_coordinate_system,
                to_coordinate_system=to_coordinate_system,
            )

            home_team_id = metadata["home_team"]["id"]
            away_team_id = metadata["away_team"]["id"]

            players = {"HOME": {}, "AWAY": {}}

            home_team = Team(
                team_id=home_team_id,
                name=metadata["home_team"]["name"],
                ground=Ground.HOME,
            )
            self.home_team = home_team

            away_team = Team(
                team_id=away_team_id,
                name=metadata["away_team"]["name"],
                ground=Ground.AWAY,
            )
            self.away_team = away_team

            teams = [home_team, away_team]

            for player_id in player_dict.keys():
                player = player_dict.get(player_id)
                team_id = player["team_id"]

                if team_id == home_team_id:
                    team_string = "HOME"
                    team = home_team
                elif team_id == away_team_id:
                    team_string = "AWAY"
                    team = away_team

                players[team_string][player_id] = Player(
                    player_id=f"{team.ground}_{player['number']}",
                    team=team,
                    jersey_no=player["number"],
                    name=f"{player['first_name']} {player['last_name']}",
                    first_name=player["first_name"],
                    last_name=player["last_name"],
                    starting=player["start_time"] == "00:00:00",
                    position=Position(
                        position_id=player["player_role"].get("id"),
                        name=player["player_role"].get("name"),
                        coordinates=None,
                    ),
                    attributes={},
                )

            home_team.players = list(players["HOME"].values())
            away_team.players = list(players["AWAY"].values())

        anon_players = {"HOME": {}, "AWAY": {}}

        with performance_logging("Loading data", logger=logger):

            def _iter():
                n = 0
                sample = 1.0 / sample_rate

                for frame in raw_data:
                    frame_period = frame["period"]

                    if frame_period is not None:
                        if n % sample == 0:
                            yield frame
                        n += 1

        frames = []

        n_frames = 0
        for _frame in _iter():
            # include frame if there is any tracking data, players or ball.
            # or if include_empty_frames == True
            if include_empty_frames or len(_frame["data"]) > 0:
                frame = self._get_frame_data(
                    teams,
                    teamdict,
                    players,
                    player_id_to_team_dict,
                    periods,
                    player_dict,
                    anon_players,
                    ball_id,
                    referee_dict,
                    _frame,
                )

                frame = transformer.transform_frame(frame)

                frames.append(frame)
                n_frames += 1

                if limit and n_frames >= limit:
                    break

        self._set_skillcorner_attacking_directions(frames, periods)

        frame_rate = 10

        orientation = (
            Orientation.HOME_TEAM
            if periods[1].attacking_direction == AttackingDirection.HOME_AWAY
            else Orientation.AWAY_TEAM
        )

        metadata = Metadata(
            teams=teams,
            periods=periods,
            pitch_dimensions=to_coordinate_system.pitch_dimensions,
            score=Score(
                home=metadata["home_team_score"],
                away=metadata["away_team_score"],
            ),
            frame_rate=frame_rate,
            orientation=orientation,
            provider=Provider.SKILLCORNER,
            flags=~(DatasetFlag.BALL_STATE | DatasetFlag.BALL_OWNING_TEAM),
            coordinate_system=to_coordinate_system,
        )

        return TrackingDataset(
            records=frames,
            metadata=metadata,
        )

    def serialize(self, dataset: TrackingDataset) -> Tuple[str, str]:
        raise NotImplementedError
