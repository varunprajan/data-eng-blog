import numpy as np
import pandas as pd
import statsmodels.formula.api as smf


# adapted https://github.com/matteocourthoud/Blog-Posts/blob/9b5ff8276b8a197ccbbe97fa1e26f3e87871544d/notebooks/src/dgp_collection.py
class DataGenerationContinuousMetric:
    def __init__(
        self,
        y0_intercept=5,
        y0_bias=0,
        y1_offset=3,
        treatment_effect=2,
        y0_noise_dist="normal",
    ):
        self.y0_intercept = y0_intercept
        self.y0_bias = y0_bias
        self.y1_offset = y1_offset
        self.treatment_effect = treatment_effect
        self.y0_noise_dist = y0_noise_dist

    def generate_data(self, N=10000, seed=1):
        rng = np.random.default_rng(seed)

        # Individuals
        i = range(N)

        # Treatment status (0 = control, 1 = treatment)
        experiment_group = rng.choice(a=[0, 1], size=N)

        # Pre-treatment value of metric (Gaussian)
        if self.y0_noise_dist == "normal":
            y0_noise = rng.normal(loc=0.0, scale=1.0, size=N)
        elif self.y0_noise_dist == "lognormal":
            y0_noise = rng.lognormal(mean=0.0, sigma=1.0, size=N)
        y0 = self.y0_intercept + self.y0_bias * experiment_group + y0_noise

        # Metric (linearly related to pre-treatment value of metric)
        y1_noise = rng.normal(loc=0.0, scale=1.0, size=N)
        y1 = y0 + self.y1_offset + self.treatment_effect * experiment_group + y1_noise

        # Generate the dataframe
        # use y0 (pre-treatment value of metric) as the covariate, X
        # rename y1 to y
        df = pd.DataFrame(
            {"i": i, "experiment_group": experiment_group, "X": y0, "y": y1}
        )

        return df


def naive(df):
    estimate = smf.ols("y ~ experiment_group", data=df).fit().params.iloc[1]
    return estimate


def cuped(df):
    df = df.copy()
    df["y_tilde"] = smf.ols("y ~ X", data=df).fit().resid + np.mean(df["X"])
    estimate = smf.ols("y_tilde ~ experiment_group", data=df).fit().params.iloc[1]
    return estimate


def cuped_binary(df):
    df = df.copy()
    df["y_tilde"] = smf.logit("y ~ X", data=df).fit(disp=0).resid_response
    estimate = smf.ols("y_tilde ~ experiment_group", data=df).fit().params.iloc[1]
    return estimate


def cuped_advanced(df):
    df_treatment = df.query("experiment_group == 1")
    df_control = df.query("experiment_group == 0")
    p_treatment = df_treatment.shape[0] / df.shape[0]
    p_control = df_control.shape[0] / df.shape[0]
    theta_treatment = smf.ols("y ~ X", data=df_treatment).fit().params.iloc[1]
    theta_control = smf.ols("y ~ X", data=df_control).fit().params.iloc[1]
    theta_avg = p_control * theta_control + p_treatment * theta_treatment
    y_tilde_avg_treatment = df_treatment.assign(
        y_tilde=lambda df: df["y"] - theta_avg * df["X"]
    )["y_tilde"].mean()
    y_tilde_avg_control = df_control.assign(
        y_tilde=lambda df: df["y"] - theta_avg * df["X"]
    )["y_tilde"].mean()
    estimate = y_tilde_avg_treatment - y_tilde_avg_control
    return estimate


def ancova2(df):
    X_mean = df["X"].mean()
    df = df.copy().assign(X_demeaned=lambda df: df["X"] - X_mean)
    estimate = (
        smf.ols(
            "y ~ experiment_group + X_demeaned + experiment_group * X_demeaned", data=df
        )
        .fit()
        .params.iloc[1]
    )
    return estimate


def simulate(
    dgp, estimators, N_trials=1000, sample_size=10000, winsorize_q=None, binarize_q=None
):
    results = []

    # Conduct N trials, generating new data for each one
    for trial_num in range(N_trials):
        # Generate dataframe
        df = dgp.generate_data(seed=trial_num, N=sample_size)

        # apply winsorization/binarization (optional)
        if winsorize_q is not None:
            cutpoint = df["y"].quantile(winsorize_q)
            df = df.assign(
                y=lambda df: df["y"].clip(upper=cutpoint),
            )
        elif binarize_q is not None:
            cutpoint = df["y"].quantile(binarize_q)
            df = df.assign(
                y=lambda df: (df["y"] > cutpoint).astype(int),
            )

        # Iterate over estimators, generating estimate for each
        for estimator, estimator_func in estimators:
            estimate = estimator_func(df)
            result = {
                "trial_num": trial_num,
                "estimator": estimator,
                "effect_size": estimate,
            }
            results.append(result)

    return pd.DataFrame(results)
