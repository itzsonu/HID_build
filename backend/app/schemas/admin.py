from pydantic import BaseModel, Field


class EnrollmentSummary(BaseModel):
    user_id: str
    template_count: int = Field(ge=1)


class EnrollmentListResponse(BaseModel):
    enrollments: list[EnrollmentSummary]


class EnrollmentDeleteRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


class EnrollmentDeleteResponse(BaseModel):
    deleted: bool
    user_id: str
