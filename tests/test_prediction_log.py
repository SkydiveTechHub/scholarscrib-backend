from app.database.models import PredictionLog


def test_prediction_log_columns_use_the_camel_case_names():
    columns = {column.name for column in PredictionLog.__table__.columns}
    assert columns == {
        "id",
        "studentId",
        "subjectId",
        "topicId",
        "questionId",
        "source",
        "predictedP",
        "confidence",
        "accObservations",
        "difficulty",
        "correct",
        "scoringVersion",
        "occurredAt",
    }
    indexes = {index.name for index in PredictionLog.__table__.indexes}
    assert indexes == {
        "PredictionLog_version_occurred_idx",
        "PredictionLog_topic_idx",
    }
