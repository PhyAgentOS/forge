use std::collections::BTreeMap;
use std::error::Error;
use std::fmt;
use std::sync::{Mutex, MutexGuard};

pub const EVENT_METRIC_NAME: &str = "forge_observability_events_total";
pub const MAX_VALUE_NS: u64 = i64::MAX as u64;
pub const MAX_COUNTER: u64 = i64::MAX as u64;
pub const MAX_BUCKETS: usize = 64;
pub const DEFAULT_BUCKETS_NS: [u64; 16] = [
    1_000_000,
    2_000_000,
    4_000_000,
    8_000_000,
    16_000_000,
    32_000_000,
    64_000_000,
    128_000_000,
    256_000_000,
    512_000_000,
    1_024_000_000,
    2_048_000_000,
    4_096_000_000,
    8_192_000_000,
    16_384_000_000,
    32_768_000_000,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum LatencyMetric {
    Hop,
    EndToEnd,
    NodeTurnaround,
}

impl LatencyMetric {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Hop => "forge_hop_latency_seconds",
            Self::EndToEnd => "forge_e2e_latency_seconds",
            Self::NodeTurnaround => "forge_node_turnaround_seconds",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum EventReason {
    Received,
    Published,
    PublishFailed,
    MissingContext,
    InvalidMetadata,
    InvalidVersion,
    UnknownVersion,
    InvalidPublishTime,
    MissingOrigin,
    InvalidOriginTime,
    InvalidOriginId,
    InvalidParent,
    ClockError,
    NegativeHop,
    NegativeEndToEnd,
    NegativeTurnaround,
    SinkError,
    ConflictingOrigin,
}

impl EventReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Received => "received",
            Self::Published => "published",
            Self::PublishFailed => "publish_failed",
            Self::MissingContext => "missing_context",
            Self::InvalidMetadata => "invalid_metadata",
            Self::InvalidVersion => "invalid_version",
            Self::UnknownVersion => "unknown_version",
            Self::InvalidPublishTime => "invalid_publish_time",
            Self::MissingOrigin => "missing_origin",
            Self::InvalidOriginTime => "invalid_origin_time",
            Self::InvalidOriginId => "invalid_origin_id",
            Self::InvalidParent => "invalid_parent",
            Self::ClockError => "clock_error",
            Self::NegativeHop => "negative_hop",
            Self::NegativeEndToEnd => "negative_e2e",
            Self::NegativeTurnaround => "negative_turnaround",
            Self::SinkError => "sink_error",
            Self::ConflictingOrigin => "conflicting_origin",
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MetricLabels<'a> {
    pub input_id: &'a str,
    pub output_id: &'a str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MetricsConfig {
    pub max_series: usize,
    pub max_label_bytes: usize,
    pub buckets_ns: Vec<u64>,
}

impl Default for MetricsConfig {
    fn default() -> Self {
        Self {
            max_series: 1024,
            max_label_bytes: 128,
            buckets_ns: DEFAULT_BUCKETS_NS.to_vec(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MetricsConfigError {
    InvalidMaxSeries,
    InvalidMaxLabelBytes,
    InvalidBucketCount,
    InvalidBucketBound { index: usize },
}

impl fmt::Display for MetricsConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidMaxSeries => {
                write!(formatter, "max_series must be in [1, MAX_COUNTER]")
            }
            Self::InvalidMaxLabelBytes => {
                write!(formatter, "max_label_bytes must be in [1, MAX_COUNTER]")
            }
            Self::InvalidBucketCount => write!(
                formatter,
                "buckets_ns must contain between 1 and MAX_BUCKETS bounds"
            ),
            Self::InvalidBucketBound { index } => write!(
                formatter,
                "bucket bound at index {index} must be in [1, MAX_VALUE_NS] and strictly greater than its predecessor"
            ),
        }
    }
}

impl Error for MetricsConfigError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MetricsSnapshot {
    pub histograms: Vec<HistogramSnapshot>,
    pub counters: Vec<CounterSnapshot>,
    pub invalid_inputs: u64,
    pub rejected_series: u64,
    pub saturated_updates: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HistogramSnapshot {
    pub metric: LatencyMetric,
    pub input_id: Box<str>,
    pub output_id: Box<str>,
    pub count: u64,
    pub sum_ns: u128,
    pub min_ns: u64,
    pub max_ns: u64,
    pub buckets: Vec<BucketSnapshot>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BucketSnapshot {
    pub le_ns: Option<u64>,
    pub count: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CounterSnapshot {
    pub reason: EventReason,
    pub input_id: Box<str>,
    pub output_id: Box<str>,
    pub count: u64,
}

#[derive(Debug)]
struct Histogram {
    // The final bin is overflow; finite bins are non-cumulative.
    bins: Vec<u64>,
    count: u64,
    sum_ns: u128,
    min_ns: u64,
    max_ns: u64,
}

type LabelSeries<T> = BTreeMap<Box<str>, BTreeMap<Box<str>, T>>;

#[derive(Debug, Default)]
struct State {
    histograms: BTreeMap<LatencyMetric, LabelSeries<Histogram>>,
    counters: BTreeMap<EventReason, LabelSeries<u64>>,
    series_count: usize,
    invalid_inputs: u64,
    rejected_series: u64,
    saturated_updates: u64,
}

pub struct BoundedMetricsSink {
    max_series: usize,
    max_label_bytes: usize,
    buckets_ns: Box<[u64]>,
    state: Mutex<State>,
}

impl BoundedMetricsSink {
    pub fn new(config: MetricsConfig) -> Result<Self, MetricsConfigError> {
        if config.max_series == 0 || config.max_series as u128 > MAX_COUNTER as u128 {
            return Err(MetricsConfigError::InvalidMaxSeries);
        }
        if config.max_label_bytes == 0 || config.max_label_bytes as u128 > MAX_COUNTER as u128 {
            return Err(MetricsConfigError::InvalidMaxLabelBytes);
        }
        if !(1..=MAX_BUCKETS).contains(&config.buckets_ns.len()) {
            return Err(MetricsConfigError::InvalidBucketCount);
        }

        let mut previous = 0;
        for (index, &bound) in config.buckets_ns.iter().enumerate() {
            if bound == 0 || bound > MAX_VALUE_NS || bound <= previous {
                return Err(MetricsConfigError::InvalidBucketBound { index });
            }
            previous = bound;
        }

        Ok(Self {
            max_series: config.max_series,
            max_label_bytes: config.max_label_bytes,
            buckets_ns: config.buckets_ns.into_boxed_slice(),
            state: Mutex::new(State::default()),
        })
    }

    pub fn observe(&self, metric: LatencyMetric, value_ns: u64, labels: MetricLabels<'_>) {
        let mut state = self.lock_state();
        if value_ns > MAX_VALUE_NS || !self.valid_labels(labels) {
            increment_diagnostic(&mut state.invalid_inputs);
            return;
        }

        if let Some(histogram) = state
            .histograms
            .get_mut(&metric)
            .and_then(|inputs| inputs.get_mut(labels.input_id))
            .and_then(|outputs| outputs.get_mut(labels.output_id))
        {
            if histogram.count == MAX_COUNTER {
                increment_diagnostic(&mut state.saturated_updates);
            } else {
                update_histogram(histogram, value_ns, &self.buckets_ns);
            }
            return;
        }

        if state.series_count >= self.max_series {
            increment_diagnostic(&mut state.rejected_series);
            return;
        }

        let mut histogram = Histogram {
            bins: vec![0; self.buckets_ns.len() + 1],
            count: 0,
            sum_ns: 0,
            min_ns: value_ns,
            max_ns: value_ns,
        };
        update_histogram(&mut histogram, value_ns, &self.buckets_ns);
        state
            .histograms
            .entry(metric)
            .or_default()
            .entry(labels.input_id.into())
            .or_default()
            .insert(labels.output_id.into(), histogram);
        state.series_count += 1;
    }

    pub fn increment(&self, reason: EventReason, labels: MetricLabels<'_>) {
        let mut state = self.lock_state();
        if !self.valid_labels(labels) {
            increment_diagnostic(&mut state.invalid_inputs);
            return;
        }

        if let Some(count) = state
            .counters
            .get_mut(&reason)
            .and_then(|inputs| inputs.get_mut(labels.input_id))
            .and_then(|outputs| outputs.get_mut(labels.output_id))
        {
            if *count == MAX_COUNTER {
                increment_diagnostic(&mut state.saturated_updates);
            } else {
                *count += 1;
            }
            return;
        }

        if state.series_count >= self.max_series {
            increment_diagnostic(&mut state.rejected_series);
            return;
        }

        state
            .counters
            .entry(reason)
            .or_default()
            .entry(labels.input_id.into())
            .or_default()
            .insert(labels.output_id.into(), 1);
        state.series_count += 1;
    }

    pub fn snapshot(&self) -> MetricsSnapshot {
        let state = self.lock_state();
        self.snapshot_from_state(&state)
    }

    pub fn take_snapshot(&self) -> MetricsSnapshot {
        let mut state = self.lock_state();
        let snapshot = self.snapshot_from_state(&state);
        *state = State::default();
        snapshot
    }

    fn valid_labels(&self, labels: MetricLabels<'_>) -> bool {
        labels.input_id.len() <= self.max_label_bytes
            && labels.output_id.len() <= self.max_label_bytes
    }

    fn lock_state(&self) -> MutexGuard<'_, State> {
        self.state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn snapshot_from_state(&self, state: &State) -> MetricsSnapshot {
        let mut histograms = Vec::with_capacity(state.histograms.len());
        for (&metric, inputs) in &state.histograms {
            for (input_id, outputs) in inputs {
                for (output_id, histogram) in outputs {
                    let mut cumulative = 0;
                    let mut buckets = Vec::with_capacity(self.buckets_ns.len() + 1);
                    for (&le_ns, &count) in self.buckets_ns.iter().zip(&histogram.bins) {
                        cumulative += count;
                        buckets.push(BucketSnapshot {
                            le_ns: Some(le_ns),
                            count: cumulative,
                        });
                    }
                    buckets.push(BucketSnapshot {
                        le_ns: None,
                        count: histogram.count,
                    });
                    histograms.push(HistogramSnapshot {
                        metric,
                        input_id: input_id.clone(),
                        output_id: output_id.clone(),
                        count: histogram.count,
                        sum_ns: histogram.sum_ns,
                        min_ns: histogram.min_ns,
                        max_ns: histogram.max_ns,
                        buckets,
                    });
                }
            }
        }

        let mut counters = Vec::with_capacity(state.counters.len());
        for (&reason, inputs) in &state.counters {
            for (input_id, outputs) in inputs {
                for (output_id, &count) in outputs {
                    counters.push(CounterSnapshot {
                        reason,
                        input_id: input_id.clone(),
                        output_id: output_id.clone(),
                        count,
                    });
                }
            }
        }

        MetricsSnapshot {
            histograms,
            counters,
            invalid_inputs: state.invalid_inputs,
            rejected_series: state.rejected_series,
            saturated_updates: state.saturated_updates,
        }
    }
}

fn increment_diagnostic(value: &mut u64) {
    if *value < MAX_COUNTER {
        *value += 1;
    }
}

fn update_histogram(histogram: &mut Histogram, value_ns: u64, buckets_ns: &[u64]) {
    let bin = match buckets_ns.binary_search(&value_ns) {
        Ok(index) | Err(index) => index,
    };
    histogram.bins[bin] += 1;
    histogram.count += 1;
    histogram.sum_ns += u128::from(value_ns);
    histogram.min_ns = histogram.min_ns.min(value_ns);
    histogram.max_ns = histogram.max_ns.max(value_ns);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::panic::{self, AssertUnwindSafe};
    use std::sync::{Arc, Barrier};
    use std::thread;

    const NO_LABELS: MetricLabels<'static> = MetricLabels {
        input_id: "",
        output_id: "",
    };

    fn config_with_buckets(buckets_ns: Vec<u64>) -> MetricsConfig {
        MetricsConfig {
            buckets_ns,
            ..MetricsConfig::default()
        }
    }

    #[test]
    fn vocabulary_and_defaults_match_the_protocol() {
        assert_eq!(EVENT_METRIC_NAME, "forge_observability_events_total");
        assert_eq!(MAX_VALUE_NS, i64::MAX as u64);
        assert_eq!(MAX_COUNTER, i64::MAX as u64);
        assert_eq!(MAX_BUCKETS, 64);
        for (index, bound) in DEFAULT_BUCKETS_NS.iter().enumerate() {
            assert_eq!(*bound, 1_000_000 * (1_u64 << index));
        }

        assert_eq!(LatencyMetric::Hop.as_str(), "forge_hop_latency_seconds");
        assert_eq!(
            LatencyMetric::EndToEnd.as_str(),
            "forge_e2e_latency_seconds"
        );
        assert_eq!(
            LatencyMetric::NodeTurnaround.as_str(),
            "forge_node_turnaround_seconds"
        );
        let reasons = [
            (EventReason::Received, "received"),
            (EventReason::Published, "published"),
            (EventReason::PublishFailed, "publish_failed"),
            (EventReason::MissingContext, "missing_context"),
            (EventReason::InvalidMetadata, "invalid_metadata"),
            (EventReason::InvalidVersion, "invalid_version"),
            (EventReason::UnknownVersion, "unknown_version"),
            (EventReason::InvalidPublishTime, "invalid_publish_time"),
            (EventReason::MissingOrigin, "missing_origin"),
            (EventReason::InvalidOriginTime, "invalid_origin_time"),
            (EventReason::InvalidOriginId, "invalid_origin_id"),
            (EventReason::InvalidParent, "invalid_parent"),
            (EventReason::ClockError, "clock_error"),
            (EventReason::NegativeHop, "negative_hop"),
            (EventReason::NegativeEndToEnd, "negative_e2e"),
            (EventReason::NegativeTurnaround, "negative_turnaround"),
            (EventReason::SinkError, "sink_error"),
            (EventReason::ConflictingOrigin, "conflicting_origin"),
        ];
        for (reason, expected) in reasons {
            assert_eq!(reason.as_str(), expected);
        }

        assert_eq!(
            MetricsConfig::default(),
            MetricsConfig {
                max_series: 1024,
                max_label_bytes: 128,
                buckets_ns: DEFAULT_BUCKETS_NS.to_vec(),
            }
        );
        assert_eq!(MetricLabels::default(), NO_LABELS);
    }

    #[test]
    fn validates_configuration() {
        let valid = [
            vec![1],
            vec![1, MAX_VALUE_NS],
            vec![MAX_VALUE_NS],
            (1..=MAX_BUCKETS as u64).collect(),
        ];
        for buckets_ns in valid {
            assert!(BoundedMetricsSink::new(config_with_buckets(buckets_ns)).is_ok());
        }

        let error = BoundedMetricsSink::new(MetricsConfig {
            max_series: 0,
            ..MetricsConfig::default()
        });
        assert!(matches!(error, Err(MetricsConfigError::InvalidMaxSeries)));
        let error = BoundedMetricsSink::new(MetricsConfig {
            max_label_bytes: 0,
            ..MetricsConfig::default()
        });
        assert!(matches!(
            error,
            Err(MetricsConfigError::InvalidMaxLabelBytes)
        ));
        if usize::MAX as u128 > MAX_COUNTER as u128 {
            let error = BoundedMetricsSink::new(MetricsConfig {
                max_series: usize::MAX,
                ..MetricsConfig::default()
            });
            assert!(matches!(error, Err(MetricsConfigError::InvalidMaxSeries)));
            let error = BoundedMetricsSink::new(MetricsConfig {
                max_label_bytes: usize::MAX,
                ..MetricsConfig::default()
            });
            assert!(matches!(
                error,
                Err(MetricsConfigError::InvalidMaxLabelBytes)
            ));
        }
        for buckets_ns in [vec![], vec![1; MAX_BUCKETS + 1]] {
            let error = BoundedMetricsSink::new(config_with_buckets(buckets_ns));
            assert!(matches!(error, Err(MetricsConfigError::InvalidBucketCount)));
        }
        for (buckets_ns, index) in [
            (vec![0], 0),
            (vec![2, 1], 1),
            (vec![1, 1], 1),
            (vec![MAX_VALUE_NS + 1], 0),
        ] {
            let error = BoundedMetricsSink::new(config_with_buckets(buckets_ns));
            assert_eq!(
                error.err(),
                Some(MetricsConfigError::InvalidBucketBound { index })
            );
        }

        let error: &dyn Error = &MetricsConfigError::InvalidBucketCount;
        assert!(!error.to_string().is_empty());
    }

    #[test]
    fn empty_snapshot_has_no_series() {
        let sink = BoundedMetricsSink::new(MetricsConfig::default()).unwrap();
        assert_eq!(
            sink.snapshot(),
            MetricsSnapshot {
                histograms: vec![],
                counters: vec![],
                invalid_inputs: 0,
                rejected_series: 0,
                saturated_updates: 0,
            }
        );
    }

    #[test]
    fn buckets_are_inclusive_cumulative_and_have_overflow() {
        let sink = BoundedMetricsSink::new(config_with_buckets(vec![10, 20, 40])).unwrap();
        let values = [0, 9, 10, 11, 20, 40, 41];
        for value in values {
            sink.observe(
                LatencyMetric::EndToEnd,
                value,
                MetricLabels {
                    input_id: "camera",
                    output_id: "pose",
                },
            );
        }

        assert_eq!(
            sink.snapshot().histograms,
            vec![HistogramSnapshot {
                metric: LatencyMetric::EndToEnd,
                input_id: "camera".into(),
                output_id: "pose".into(),
                count: 7,
                sum_ns: 131,
                min_ns: 0,
                max_ns: 41,
                buckets: vec![
                    BucketSnapshot {
                        le_ns: Some(10),
                        count: 3,
                    },
                    BucketSnapshot {
                        le_ns: Some(20),
                        count: 5,
                    },
                    BucketSnapshot {
                        le_ns: Some(40),
                        count: 6,
                    },
                    BucketSnapshot {
                        le_ns: None,
                        count: 7,
                    },
                ],
            }]
        );
    }

    #[test]
    fn max_value_is_valid_and_larger_values_are_invalid() {
        let sink = BoundedMetricsSink::new(config_with_buckets(vec![1])).unwrap();
        sink.observe(LatencyMetric::Hop, MAX_VALUE_NS, NO_LABELS);
        sink.observe(LatencyMetric::Hop, MAX_VALUE_NS + 1, NO_LABELS);

        let snapshot = sink.snapshot();
        assert_eq!(snapshot.invalid_inputs, 1);
        assert_eq!(snapshot.histograms[0].count, 1);
        assert_eq!(snapshot.histograms[0].sum_ns, u128::from(MAX_VALUE_NS));
        assert_eq!(
            snapshot.histograms[0].buckets,
            vec![
                BucketSnapshot {
                    le_ns: Some(1),
                    count: 0,
                },
                BucketSnapshot {
                    le_ns: None,
                    count: 1,
                },
            ]
        );
    }

    #[test]
    fn histogram_and_counter_share_the_series_limit() {
        let sink = BoundedMetricsSink::new(MetricsConfig {
            max_series: 2,
            ..MetricsConfig::default()
        })
        .unwrap();
        let labels = MetricLabels {
            input_id: "in",
            output_id: "out",
        };
        sink.observe(LatencyMetric::Hop, 1, labels);
        sink.increment(EventReason::Received, labels);
        sink.observe(LatencyMetric::EndToEnd, 1, labels);
        sink.increment(EventReason::Published, labels);
        sink.observe(LatencyMetric::Hop, 2, labels);
        sink.increment(EventReason::Received, labels);

        let snapshot = sink.snapshot();
        assert_eq!(snapshot.rejected_series, 2);
        assert_eq!(snapshot.histograms[0].count, 2);
        assert_eq!(snapshot.counters[0].count, 2);
    }

    #[test]
    fn labels_are_limited_by_utf8_bytes_without_truncation() {
        let sink = BoundedMetricsSink::new(MetricsConfig {
            max_label_bytes: 3,
            ..MetricsConfig::default()
        })
        .unwrap();
        sink.observe(
            LatencyMetric::Hop,
            1,
            MetricLabels {
                input_id: "端",
                output_id: "abc",
            },
        );
        sink.increment(
            EventReason::Received,
            MetricLabels {
                input_id: "éé",
                output_id: "abc",
            },
        );
        sink.observe(
            LatencyMetric::Hop,
            1,
            MetricLabels {
                input_id: "abcd",
                output_id: "abc",
            },
        );

        let snapshot = sink.snapshot();
        assert_eq!(snapshot.invalid_inputs, 2);
        assert_eq!(snapshot.histograms.len(), 1);
        assert_eq!(&*snapshot.histograms[0].input_id, "端");
        assert!(snapshot.counters.is_empty());
    }

    #[test]
    fn invalid_input_precedes_capacity_rejection() {
        let sink = BoundedMetricsSink::new(MetricsConfig {
            max_series: 1,
            max_label_bytes: 1,
            ..MetricsConfig::default()
        })
        .unwrap();
        sink.increment(EventReason::Received, NO_LABELS);
        sink.observe(
            LatencyMetric::Hop,
            MAX_VALUE_NS + 1,
            MetricLabels {
                input_id: "too long",
                output_id: "",
            },
        );
        sink.increment(
            EventReason::Published,
            MetricLabels {
                input_id: "too long",
                output_id: "",
            },
        );

        let snapshot = sink.snapshot();
        assert_eq!(snapshot.invalid_inputs, 2);
        assert_eq!(snapshot.rejected_series, 0);
    }

    #[test]
    fn snapshots_are_deterministic_and_detached() {
        let sink = BoundedMetricsSink::new(MetricsConfig::default()).unwrap();
        for labels in [
            MetricLabels {
                input_id: "z",
                output_id: "a",
            },
            MetricLabels {
                input_id: "a",
                output_id: "z",
            },
        ] {
            sink.observe(LatencyMetric::NodeTurnaround, 1, labels);
            sink.observe(LatencyMetric::Hop, 1, labels);
            sink.increment(EventReason::Published, labels);
            sink.increment(EventReason::Received, labels);
        }

        let first = sink.snapshot();
        assert_eq!(first, sink.snapshot());
        assert_eq!(first.histograms[0].metric, LatencyMetric::Hop);
        assert_eq!(&*first.histograms[0].input_id, "a");
        assert_eq!(first.counters[0].reason, EventReason::Received);
        assert_eq!(&*first.counters[0].input_id, "a");

        sink.observe(LatencyMetric::Hop, 1, NO_LABELS);
        assert_ne!(first, sink.snapshot());
    }

    #[test]
    fn take_snapshot_clears_everything_and_releases_capacity() {
        let sink = BoundedMetricsSink::new(MetricsConfig {
            max_series: 1,
            ..MetricsConfig::default()
        })
        .unwrap();
        sink.observe(LatencyMetric::Hop, 5, NO_LABELS);
        sink.increment(EventReason::Received, NO_LABELS);
        sink.observe(LatencyMetric::Hop, MAX_VALUE_NS + 1, NO_LABELS);
        let before = sink.snapshot();

        assert_eq!(sink.take_snapshot(), before);
        assert_eq!(
            sink.snapshot(),
            MetricsSnapshot {
                histograms: vec![],
                counters: vec![],
                invalid_inputs: 0,
                rejected_series: 0,
                saturated_updates: 0,
            }
        );

        sink.increment(EventReason::Published, NO_LABELS);
        assert_eq!(sink.snapshot().counters.len(), 1);
    }

    #[test]
    fn counters_and_diagnostics_saturate_without_changing_series() {
        let sink = BoundedMetricsSink::new(MetricsConfig {
            max_series: 2,
            buckets_ns: vec![MAX_VALUE_NS],
            ..MetricsConfig::default()
        })
        .unwrap();
        sink.observe(LatencyMetric::Hop, MAX_VALUE_NS, NO_LABELS);
        sink.increment(EventReason::Received, NO_LABELS);
        {
            let mut state = sink.lock_state();
            let histogram = state
                .histograms
                .get_mut(&LatencyMetric::Hop)
                .unwrap()
                .get_mut("")
                .unwrap()
                .get_mut("")
                .unwrap();
            histogram.count = MAX_COUNTER;
            histogram.bins[0] = MAX_COUNTER;
            histogram.sum_ns = u128::from(MAX_COUNTER) * u128::from(MAX_VALUE_NS);
            *state
                .counters
                .get_mut(&EventReason::Received)
                .unwrap()
                .get_mut("")
                .unwrap()
                .get_mut("")
                .unwrap() = MAX_COUNTER;
            state.invalid_inputs = MAX_COUNTER - 1;
            state.rejected_series = MAX_COUNTER;
            state.saturated_updates = MAX_COUNTER - 1;
        }
        let series_before = sink.snapshot();

        sink.observe(LatencyMetric::Hop, 0, NO_LABELS);
        sink.increment(EventReason::Received, NO_LABELS);
        sink.observe(LatencyMetric::Hop, MAX_VALUE_NS + 1, NO_LABELS);
        sink.observe(
            LatencyMetric::EndToEnd,
            1,
            MetricLabels {
                input_id: "new",
                output_id: "",
            },
        );
        let after = sink.snapshot();

        assert_eq!(after.histograms, series_before.histograms);
        assert_eq!(after.counters, series_before.counters);
        assert_eq!(after.invalid_inputs, MAX_COUNTER);
        assert_eq!(after.rejected_series, MAX_COUNTER);
        assert_eq!(after.saturated_updates, MAX_COUNTER);
    }

    #[test]
    fn updates_are_thread_safe() {
        const THREADS: usize = 8;
        const UPDATES: usize = 1_000;
        let sink = Arc::new(BoundedMetricsSink::new(config_with_buckets(vec![5, 10])).unwrap());
        let barrier = Arc::new(Barrier::new(THREADS));
        let mut workers = Vec::new();
        for _ in 0..THREADS {
            let sink = Arc::clone(&sink);
            let barrier = Arc::clone(&barrier);
            workers.push(thread::spawn(move || {
                barrier.wait();
                for _ in 0..UPDATES {
                    sink.observe(LatencyMetric::Hop, 10, NO_LABELS);
                    sink.increment(EventReason::Received, NO_LABELS);
                }
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }

        let snapshot = sink.snapshot();
        let expected = (THREADS * UPDATES) as u64;
        assert_eq!(snapshot.histograms[0].count, expected);
        assert_eq!(snapshot.histograms[0].sum_ns, u128::from(expected) * 10);
        assert_eq!(snapshot.histograms[0].buckets[0].count, 0);
        assert_eq!(snapshot.histograms[0].buckets[1].count, expected);
        assert_eq!(snapshot.histograms[0].buckets[2].count, expected);
        assert_eq!(snapshot.counters[0].count, expected);
    }

    #[test]
    fn poisoned_mutex_is_recovered() {
        let sink = BoundedMetricsSink::new(MetricsConfig::default()).unwrap();
        let result = panic::catch_unwind(AssertUnwindSafe(|| {
            let _guard = sink.state.lock().unwrap();
            panic!("poison state for test");
        }));
        assert!(result.is_err());

        sink.increment(EventReason::SinkError, NO_LABELS);
        assert_eq!(sink.snapshot().counters[0].count, 1);
    }
}
