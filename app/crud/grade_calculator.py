def calculate_grade(marks: float, total_marks: float) -> str:
    """
    Calculate letter grade based on refined 5-point scale:
    | Marks (%) | Grade |
    | 85 - 100  | A     |
    | 70 - 84.9 | B     |
    | 55 - 69.9 | C     |
    | 40 - 54.9 | D     |
    | Below 40  | F     |
    """
    if total_marks <= 0:
        return "N/A"
        
    percentage = (marks / total_marks) * 100.0
    
    if percentage >= 85: return "A"
    if percentage >= 70: return "B"
    if percentage >= 55: return "C"
    if percentage >= 40: return "D"
    return "F"
