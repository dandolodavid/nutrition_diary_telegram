from pydantic import BaseModel


class FoodItem(BaseModel):
    name: str
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


class NutritionEstimate(BaseModel):
    clarification_needed: bool
    clarification_question: str | None = None
    items: list[FoodItem] = []


class DietTargets(BaseModel):
    daily_kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    summary: str
