# Statistical Honesty in UX Research

Most commercial UX research tools apply standard frequentist statistical tests (like the t-test) to small-sample A/B tests and present the resulting p-values without context. This leads to false positives and overconfidence in decisions made on very little data.

This project was built to be statistically honest. It deliberately uses non-parametric tests, bootstrapped confidence intervals, and a three-state verdict to prevent you from lying to yourself.

## 1. The Sign Test

Instead of a t-test (which assumes your data is normally distributed—a terrible assumption for task completion times or ordinal ratings), we use the **Sign Test**. 

The Sign Test is beautifully simple: it only cares if Variant B was *better* than Variant A. It ignores the magnitude of the difference. If a participant was 1 second faster in B, that's a "+1". If they were 50 seconds slower, that's a "-1". 

By throwing away the magnitude, we throw away the effect of extreme outliers. One participant getting lost and taking 5 minutes to complete a 10-second task will completely skew a t-test. The Sign Test simply counts it as a single "B was worse" event.

## 2. Why 8 Participants Isn't Enough

The Sign Test requires a minimum number of consistent observations to reach statistical significance. 

If you test 5 participants and all 5 perform better on Variant B, the probability of that happening by random chance (if the variants were identical) is $(0.5)^5 = 0.031$. That's below a standard $0.05$ threshold! But wait...

We use a two-tailed test, meaning we are open to B being better OR worse. This doubles the probability to $0.0625$. Thus, with only 5 participants, **even a unanimous result is not statistically significant**.

In fact, you need at least **6 unanimous participants** just to hit $p < 0.05$. If even one participant performs better on Variant A, you need significantly more total participants to achieve significance. The platform forces you to confront this reality.

## 3. Bootstrapped Confidence Intervals

When measuring metrics like "Median Time on Task", we use **Bootstrapping** to calculate the 95% Confidence Interval (CI).

Bootstrapping means we take your sample data and randomly draw from it (with replacement) thousands of times to simulate what would happen if we ran the study over and over. We then calculate the median for each of those thousands of simulated studies and find the middle 95% range.

This requires no assumptions about the underlying distribution of your data. It simply shows you the realistic range of performance you can expect in the real world. If your CI for Variant A is [12s - 45s] and Variant B is [14s - 38s], you immediately see that the results are too noisy to declare a winner.

## 4. The Three-State Verdict

Instead of just "Significant" or "Not Significant", we use a three-state verdict for every metric:

1. **Clear Winner**: The Sign Test reached significance, AND the bootstrapped confidence interval for the difference shows a meaningful effect size (the `min_time_gain` or `min_survey_gain` you configured).
2. **No Meaningful Difference**: The test did not reach significance, OR the confidence interval proves the difference is smaller than your threshold of caring.
3. **Not Enough Data**: The confidence interval is so wide that it overlaps zero AND your minimum gain threshold. The data is too noisy or the sample size is too small to make a call.

This third state is the most important feature of the platform. It prevents you from making a decision when the only mathematically honest answer is "I don't know yet."
