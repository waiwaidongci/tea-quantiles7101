from pydantic import BaseModel, Field


class PairingStudyInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default='', max_length=2000)


class PairingReadingInput(BaseModel):
    booklet: str
    source_key: str = Field(min_length=1, max_length=100)
    sampled_at: int
    moisture: str


class PairingCorrectionInput(BaseModel):
    """一次更正可同时改动两册多条读数，后端在同一 revision 内提交。"""

    readings: list[PairingReadingInput] = Field(min_length=1, max_length=80)
    note: str = Field(default='', max_length=500)


class PairingRunInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    tolerance_minutes: int


class PairingReviewInput(BaseModel):
    note: str = Field(min_length=5, max_length=2000)
