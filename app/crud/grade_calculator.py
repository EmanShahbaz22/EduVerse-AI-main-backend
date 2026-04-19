def calculate_grade(marks: float, total_marks: float) -> str:
    """
    Calculate letter grade based on EduVerse 11-step scale:
    | Marks (%) | Grade |
    | 90 - 100  | A+    |
    | 85 - 89.9 | A     |
    | 80 - 84.9 | A-    |
    | 75 - 79.9 | B+    |
    | 70 - 74.9 | B     |
    | 65 - 69.9 | B-    |
    | 61 - 64.9 | C+    |
    | 58 - 60.9 | C     |
    | 55 - 57.9 | C-    |
    | 50 - 54.9 | D     |
    | Below 50  | F     |
    """
    if total_marks <= 0:
        return "N/A"
        
    percentage = (marks / total_marks) * 100.0
    
    if percentage >= 90: return "A+"
    if percentage >= 85: return "A"
    if percentage >= 80: return "A-"
    if percentage >= 75: return "B+"
    if percentage >= 70: return "B"
    if percentage >= 65: return "B-"
    if percentage >= 61: return "C+"
    if percentage >= 58: return "C"
    if percentage >= 55: return "C-"
    if percentage >= 50: return "D"
    return "F"
