from hermes.core.supervisor.policy import RetryPolicy


def test_should_retry_true_while_under_limit():
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(1) is True
    assert policy.should_retry(2) is True


def test_should_retry_false_at_limit():
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(3) is False


def test_next_backoff_grows_exponentially():
    policy = RetryPolicy(backoff_base_seconds=1.0, backoff_multiplier=2.0)
    assert policy.next_backoff(1) == 1.0
    assert policy.next_backoff(2) == 2.0
    assert policy.next_backoff(3) == 4.0


def test_should_retry_respects_per_task_override():
    policy = RetryPolicy(max_attempts=5)
    assert policy.should_retry(2, max_attempts=2) is False


def test_default_policy_allows_three_attempts():
    policy = RetryPolicy()
    assert policy.should_retry(1) is True
    assert policy.should_retry(2) is True
    assert policy.should_retry(3) is False
