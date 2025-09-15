"""Advanced filtering system for YouTube Ranger.

Provides sophisticated filtering capabilities for videos with support for
multiple criteria, operators, and combinations.
"""
# Created: 2025-09-13

import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

from .models import Video


logger = logging.getLogger(__name__)


class FilterOperator(Enum):
    """Supported filter operators."""
    EQUALS = "="
    NOT_EQUALS = "!="
    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    CONTAINS = "contains"
    NOT_CONTAINS = "!contains"
    REGEX = "regex"


class FilterField(Enum):
    """Filterable video fields."""
    TITLE = "title"
    CHANNEL = "channel"
    DURATION = "duration"
    DATE = "date"
    VIEWS = "views"
    DESCRIPTION = "description"
    POSITION = "position"


@dataclass
class FilterCriteria:
    """Single filter criterion."""
    field: FilterField
    operator: FilterOperator
    value: Any
    raw_expression: str = ""


class FilterParser:
    """Parses filter expressions into executable criteria."""
    
    # Regex patterns for different filter types
    DURATION_PATTERN = re.compile(
        r'duration\s*([><=!]+)\s*(?:(\d+):)?(\d+):(\d+)|duration\s*([><=!]+)\s*(\d+)s?'
    )
    CHANNEL_PATTERN = re.compile(
        r'channel\s*([=!]+|contains)\s*["\']?([^"\']+)["\']?'
    )
    DATE_PATTERN = re.compile(
        r'date\s*([><=!]+)\s*(\d{4}-\d{2}-\d{2})|date\s*([><=!]+)\s*(\d+)d'
    )
    VIEWS_PATTERN = re.compile(
        r'views?\s*([><=!]+)\s*(\d+(?:\.\d+)?[kmb]?)',
        re.IGNORECASE
    )
    TITLE_PATTERN = re.compile(
        r'title\s*(contains|!contains|regex)\s*["\']?([^"\']+)["\']?'
    )
    POSITION_PATTERN = re.compile(
        r'position\s*([><=!]+)\s*(\d+)'
    )
    
    def parse(self, expression: str) -> List[FilterCriteria]:
        """Parse a filter expression into criteria.
        
        Args:
            expression: Filter expression string
            
        Returns:
            List of FilterCriteria objects
            
        Examples:
            "duration>10:00" -> Filter for videos longer than 10 minutes
            "channel:TED" -> Filter for videos from TED channel
            "date>2024-01-01 views>1000000" -> Multiple criteria
        """
        criteria = []
        
        # Split by common separators (space, comma, AND)
        # But preserve quoted strings
        parts = self._split_expression(expression)
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
                
            criterion = self._parse_single_criterion(part)
            if criterion:
                criteria.append(criterion)
                logger.debug(f"Parsed filter: {criterion}")
            else:
                logger.warning(f"Could not parse filter expression: {part}")
        
        return criteria
    
    def _split_expression(self, expression: str) -> List[str]:
        """Split expression while preserving quoted strings."""
        parts = []
        current = []
        in_quotes = False
        quote_char = None
        
        for char in expression:
            if char in ('"', "'") and not in_quotes:
                in_quotes = True
                quote_char = char
                current.append(char)
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
                current.append(char)
            elif char in (' ', ',') and not in_quotes:
                if current:
                    parts.append(''.join(current))
                    current = []
            else:
                current.append(char)
        
        if current:
            parts.append(''.join(current))
        
        return parts
    
    def _parse_single_criterion(self, part: str) -> Optional[FilterCriteria]:
        """Parse a single filter criterion."""
        
        # Try duration filter
        match = self.DURATION_PATTERN.match(part)
        if match:
            if match.group(1):  # HH:MM:SS or MM:SS format
                op = self._parse_operator(match.group(1))
                hours = int(match.group(2) or 0)
                minutes = int(match.group(3))
                seconds = int(match.group(4))
                total_seconds = hours * 3600 + minutes * 60 + seconds
            else:  # Seconds format
                op = self._parse_operator(match.group(5))
                total_seconds = int(match.group(6))
            
            return FilterCriteria(
                field=FilterField.DURATION,
                operator=op,
                value=total_seconds,
                raw_expression=part
            )
        
        # Try channel filter
        match = self.CHANNEL_PATTERN.match(part)
        if match:
            op_str = match.group(1)
            channel = match.group(2).strip()
            
            if op_str == "contains":
                op = FilterOperator.CONTAINS
            elif op_str == "!=" or op_str == "!contains":
                op = FilterOperator.NOT_EQUALS
            else:
                op = FilterOperator.EQUALS
            
            return FilterCriteria(
                field=FilterField.CHANNEL,
                operator=op,
                value=channel,
                raw_expression=part
            )
        
        # Try date filter
        match = self.DATE_PATTERN.match(part)
        if match:
            if match.group(1):  # YYYY-MM-DD format
                op = self._parse_operator(match.group(1))
                date_str = match.group(2)
                date_value = datetime.strptime(date_str, "%Y-%m-%d")
            else:  # Relative days format (e.g., 30d)
                op = self._parse_operator(match.group(3))
                days = int(match.group(4))
                date_value = datetime.now() - timedelta(days=days)
            
            return FilterCriteria(
                field=FilterField.DATE,
                operator=op,
                value=date_value,
                raw_expression=part
            )
        
        # Try views filter
        match = self.VIEWS_PATTERN.match(part)
        if match:
            op = self._parse_operator(match.group(1))
            views_str = match.group(2).lower()
            
            # Parse shorthand (1k, 1m, 1b)
            multiplier = 1
            if views_str.endswith('k'):
                multiplier = 1000
                views_str = views_str[:-1]
            elif views_str.endswith('m'):
                multiplier = 1000000
                views_str = views_str[:-1]
            elif views_str.endswith('b'):
                multiplier = 1000000000
                views_str = views_str[:-1]
            
            views = int(float(views_str) * multiplier)
            
            return FilterCriteria(
                field=FilterField.VIEWS,
                operator=op,
                value=views,
                raw_expression=part
            )
        
        # Try title filter
        match = self.TITLE_PATTERN.match(part)
        if match:
            op_str = match.group(1)
            pattern = match.group(2).strip()
            
            if op_str == "regex":
                op = FilterOperator.REGEX
            elif op_str == "!contains":
                op = FilterOperator.NOT_CONTAINS
            else:
                op = FilterOperator.CONTAINS
            
            return FilterCriteria(
                field=FilterField.TITLE,
                operator=op,
                value=pattern,
                raw_expression=part
            )
        
        # Try position filter
        match = self.POSITION_PATTERN.match(part)
        if match:
            op = self._parse_operator(match.group(1))
            position = int(match.group(2))
            
            return FilterCriteria(
                field=FilterField.POSITION,
                operator=op,
                value=position,
                raw_expression=part
            )
        
        # Default: treat as title contains
        return FilterCriteria(
            field=FilterField.TITLE,
            operator=FilterOperator.CONTAINS,
            value=part,
            raw_expression=part
        )
    
    def _parse_operator(self, op_str: str) -> FilterOperator:
        """Parse operator string into FilterOperator."""
        op_map = {
            "=": FilterOperator.EQUALS,
            "==": FilterOperator.EQUALS,
            "!=": FilterOperator.NOT_EQUALS,
            ">": FilterOperator.GREATER_THAN,
            "<": FilterOperator.LESS_THAN,
            ">=": FilterOperator.GREATER_EQUAL,
            "<=": FilterOperator.LESS_EQUAL,
        }
        return op_map.get(op_str, FilterOperator.EQUALS)


class VideoFilter:
    """Applies filter criteria to videos."""
    
    def __init__(self):
        self.parser = FilterParser()
    
    def filter(self, videos: List[Video], expression: str) -> List[Video]:
        """Filter videos based on expression.
        
        Args:
            videos: List of videos to filter
            expression: Filter expression
            
        Returns:
            Filtered list of videos
        """
        if not expression:
            return videos
        
        criteria = self.parser.parse(expression)
        if not criteria:
            return videos
        
        filtered = []
        for video in videos:
            if self._matches_all_criteria(video, criteria):
                filtered.append(video)
        
        logger.info(f"Filtered {len(videos)} videos to {len(filtered)} using: {expression}")
        return filtered
    
    def _matches_all_criteria(self, video: Video, criteria: List[FilterCriteria]) -> bool:
        """Check if video matches all criteria (AND logic)."""
        for criterion in criteria:
            if not self._matches_criterion(video, criterion):
                return False
        return True
    
    def _matches_criterion(self, video: Video, criterion: FilterCriteria) -> bool:
        """Check if video matches a single criterion."""
        try:
            # Get the field value from the video
            if criterion.field == FilterField.TITLE:
                value = video.title.lower() if video.title else ""
                return self._apply_string_operator(value, criterion.operator, 
                                                  str(criterion.value).lower())
            
            elif criterion.field == FilterField.CHANNEL:
                value = video.channel_title.lower() if video.channel_title else ""
                return self._apply_string_operator(value, criterion.operator,
                                                  str(criterion.value).lower())
            
            elif criterion.field == FilterField.DURATION:
                if not video.duration:
                    return False
                duration_seconds = self._parse_duration(video.duration)
                return self._apply_numeric_operator(duration_seconds, criterion.operator,
                                                   criterion.value)
            
            elif criterion.field == FilterField.DATE:
                if not video.published_at:
                    return False
                return self._apply_date_operator(video.published_at, criterion.operator,
                                                criterion.value)
            
            elif criterion.field == FilterField.VIEWS:
                if video.view_count is None:
                    return False
                return self._apply_numeric_operator(video.view_count, criterion.operator,
                                                   criterion.value)
            
            elif criterion.field == FilterField.POSITION:
                return self._apply_numeric_operator(video.position, criterion.operator,
                                                   criterion.value)
            
        except Exception as e:
            logger.error(f"Error applying filter criterion: {e}")
            return False
        
        return False
    
    def _apply_string_operator(self, value: str, operator: FilterOperator, 
                               target: str) -> bool:
        """Apply string comparison operator."""
        if operator == FilterOperator.EQUALS:
            return value == target
        elif operator == FilterOperator.NOT_EQUALS:
            return value != target
        elif operator == FilterOperator.CONTAINS:
            return target in value
        elif operator == FilterOperator.NOT_CONTAINS:
            return target not in value
        elif operator == FilterOperator.REGEX:
            try:
                return bool(re.search(target, value, re.IGNORECASE))
            except re.error:
                return False
        return False
    
    def _apply_numeric_operator(self, value: float, operator: FilterOperator,
                                target: float) -> bool:
        """Apply numeric comparison operator."""
        if operator == FilterOperator.EQUALS:
            return value == target
        elif operator == FilterOperator.NOT_EQUALS:
            return value != target
        elif operator == FilterOperator.GREATER_THAN:
            return value > target
        elif operator == FilterOperator.LESS_THAN:
            return value < target
        elif operator == FilterOperator.GREATER_EQUAL:
            return value >= target
        elif operator == FilterOperator.LESS_EQUAL:
            return value <= target
        return False
    
    def _apply_date_operator(self, value: datetime, operator: FilterOperator,
                            target: datetime) -> bool:
        """Apply date comparison operator."""
        if operator == FilterOperator.EQUALS:
            return value.date() == target.date()
        elif operator == FilterOperator.NOT_EQUALS:
            return value.date() != target.date()
        elif operator == FilterOperator.GREATER_THAN:
            return value > target
        elif operator == FilterOperator.LESS_THAN:
            return value < target
        elif operator == FilterOperator.GREATER_EQUAL:
            return value >= target
        elif operator == FilterOperator.LESS_EQUAL:
            return value <= target
        return False
    
    def _parse_duration(self, duration_str: str) -> int:
        """Parse ISO 8601 duration to seconds."""
        # Handle PT#H#M#S format
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
        if match:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2) or 0)
            seconds = int(match.group(3) or 0)
            return hours * 3600 + minutes * 60 + seconds
        return 0