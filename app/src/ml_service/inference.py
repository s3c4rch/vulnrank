from __future__ import annotations

from functools import lru_cache

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml_service.models import PriorityClass


MODEL_FEATURES = ("x1", "x2")
PRIORITY_SCORES = {
    PriorityClass.LOW.value: 2.5,
    PriorityClass.MEDIUM.value: 5.5,
    PriorityClass.HIGH.value: 8.5,
}
TRAINING_FEATURES = [
    [0.5, 0.7],
    [0.8, 1.0],
    [1.2, 1.4],
    [1.0, 2.1],
    [1.8, 2.0],
    [2.2, 2.4],
    [2.5, 3.0],
    [3.2, 3.6],
    [3.8, 4.1],
    [4.0, 4.8],
    [4.6, 4.9],
    [5.1, 5.3],
    [5.5, 6.0],
    [6.2, 6.4],
    [6.6, 6.9],
    [7.2, 7.0],
    [7.8, 7.6],
    [8.1, 8.3],
    [8.9, 8.7],
    [9.4, 9.1],
]
TRAINING_LABELS = (
    [PriorityClass.LOW.value] * 6
    + [PriorityClass.MEDIUM.value] * 8
    + [PriorityClass.HIGH.value] * 6
)


@lru_cache(maxsize=1)
def get_priority_model() -> Pipeline:
    model = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=1000,
                    multi_class="auto",
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(TRAINING_FEATURES, TRAINING_LABELS)
    return model


def warm_up_model() -> None:
    get_priority_model()


def build_feature_vector(features: dict[str, float]) -> list[float]:
    missing_features = [feature_name for feature_name in MODEL_FEATURES if feature_name not in features]
    if missing_features:
        missing = ", ".join(missing_features)
        raise ValueError(f"features must include {missing}")
    return [float(features[feature_name]) for feature_name in MODEL_FEATURES]


def predict_priority(features: dict[str, float]) -> tuple[float, PriorityClass, float]:
    model = get_priority_model()
    feature_vector = build_feature_vector(features)
    classifier = model.named_steps["classifier"]
    probabilities = model.predict_proba([feature_vector])[0]
    labels = classifier.classes_
    probability_map = {
        label: probability for label, probability in zip(labels, probabilities, strict=True)
    }
    predicted_label = model.predict([feature_vector])[0]
    prediction_value = round(
        sum(PRIORITY_SCORES[label] * probability for label, probability in probability_map.items()),
        2,
    )
    confidence = round(max(probabilities), 2)
    return prediction_value, PriorityClass(predicted_label), confidence
