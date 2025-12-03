"""
Accuracy scoring module for EOL Tracker.

Calculates reliability scores for EOL records based on:
- Consensus with other models (40%)
- Data completeness (25%)
- Date validity (20%)
- Model tier (15%)
"""

import logging
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Model tier definitions
PREMIUM_MODELS = [
    'anthropic.claude-sonnet-4-20250514-v1:0',
    'us.anthropic.claude-3-7-sonnet-20250219-v1:0',
    'anthropic.claude-3-5-sonnet-20241022-v2:0'
]

MID_TIER_MODELS = [
    'amazon.nova-premier-v1:0'
]


def calculate_consensus_score(record: Dict[str, Any], peer_records: List[Dict[str, Any]]) -> float:
    """
    Calculate consensus score by comparing with other models.
    
    Args:
        record: The record to score
        peer_records: Other model results for same service-cycle
        
    Returns:
        float: Consensus score (0.0-1.0)
    """
    # Filter to different models only
    peers = [r for r in peer_records 
             if r.get('model_name') != record.get('model_name')]
    
    if not peers:
        return 0.5  # Neutral score when no peers to compare
    
    comparable_fields = ['lts', 'releaseDate', 'supportEndDate', 'eol', 'latest', 'link']
    
    total_comparisons = 0
    matching_comparisons = 0
    
    for field in comparable_fields:
        record_value = record.get(field)
        if record_value is None:
            continue
            
        for peer in peers:
            peer_value = peer.get(field)
            if peer_value is None:
                continue
                
            total_comparisons += 1
            if record_value == peer_value:
                matching_comparisons += 1
    
    if total_comparisons == 0:
        return 0.5  # No comparable data
    
    return matching_comparisons / total_comparisons


def calculate_completeness_score(record: Dict[str, Any]) -> float:
    """
    Calculate completeness score based on populated fields.
    
    Args:
        record: The record to score
        
    Returns:
        float: Completeness score (0.0-1.0)
    """
    scoreable_fields = ['lts', 'releaseDate', 'supportEndDate', 'eol', 'latest', 'link']
    
    populated_count = sum(1 for field in scoreable_fields 
                         if record.get(field) is not None)
    
    return populated_count / len(scoreable_fields)


def calculate_date_validity_score(record: Dict[str, Any]) -> float:
    """
    Calculate date validity score.
    
    Checks:
    - Valid ISO format
    - Reasonable range (1990-2050)
    - Logical consistency (EOL > release)
    
    Args:
        record: The record to score
        
    Returns:
        float: Date validity score (0.0-1.0)
    """
    score_components = []
    
    date_fields = ['releaseDate', 'supportEndDate', 'eol']
    for field in date_fields:
        date_str = record.get(field)
        if date_str is None:
            continue
            
        try:
            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            score_components.append(1.0)  # Valid format
            
            # Reasonable range check
            if 1990 <= date_obj.year <= 2050:
                score_components.append(1.0)
            else:
                score_components.append(0.0)
                
        except (ValueError, AttributeError):
            score_components.append(0.0)  # Invalid format
    
    # Check logical consistency: EOL > release
    release_date = record.get('releaseDate')
    eol_date = record.get('eol')
    
    if release_date and eol_date:
        try:
            release = datetime.fromisoformat(release_date.replace('Z', '+00:00'))
            eol = datetime.fromisoformat(eol_date.replace('Z', '+00:00'))
            if eol > release:
                score_components.append(1.0)
            else:
                score_components.append(0.0)
        except (ValueError, AttributeError):
            score_components.append(0.0)
    
    if not score_components:
        return 0.5  # No dates to validate
    
    return sum(score_components) / len(score_components)


def calculate_model_tier_score(model_id: str) -> float:
    """
    Calculate model tier score based on model capability.
    
    Args:
        model_id: Bedrock model identifier
        
    Returns:
        float: Model tier score (0.2, 0.5, or 1.0)
    """
    if model_id in PREMIUM_MODELS:
        return 1.0
    elif model_id in MID_TIER_MODELS:
        return 0.5
    else:
        return 0.2  # Standard models


def calculate_accuracy_score(
    record: Dict[str, Any],
    peer_records: List[Dict[str, Any]],
    model_id: str
) -> float:
    """
    Calculate weighted composite accuracy score.
    
    Formula:
    accuracy = 0.40 * consensus + 0.25 * completeness + 
               0.20 * date_validity + 0.15 * model_tier
    
    Args:
        record: The record to score
        peer_records: Other model results for same service-cycle
        model_id: Bedrock model identifier
        
    Returns:
        float: Accuracy score (0.0-1.0)
    """
    consensus = calculate_consensus_score(record, peer_records)
    completeness = calculate_completeness_score(record)
    date_validity = calculate_date_validity_score(record)
    model_tier = calculate_model_tier_score(model_id)
    
    accuracy = (
        0.40 * consensus +
        0.25 * completeness +
        0.20 * date_validity +
        0.15 * model_tier
    )
    
    logger.debug(
        f"Accuracy score breakdown - "
        f"consensus: {consensus:.3f}, completeness: {completeness:.3f}, "
        f"date_validity: {date_validity:.3f}, model_tier: {model_tier:.3f}, "
        f"total: {accuracy:.3f}"
    )
    
    return round(accuracy, 4)
