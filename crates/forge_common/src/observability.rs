//! Application publish/receive timing without depending on Dora or an exporter.
//!
//! The core reads only the four Forge observability fields through a lookup
//! closure. Node crates remain responsible for mapping their typed transport
//! metadata and for applying [`PublicationFields`] before sending exactly once.

use std::error::Error;
use std::fmt;
use std::process;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use crate::metrics::{BoundedMetricsSink, EventReason, LatencyMetric, MAX_VALUE_NS, MetricLabels};

pub const VERSION_KEY: &str = "forge_obs_version";
pub const PUBLISH_TIME_KEY: &str = "forge_publish_time_ns";
pub const ORIGIN_TIME_KEY: &str = "forge_origin_time_ns";
pub const ORIGIN_ID_KEY: &str = "forge_origin_id";
pub const OBSERVATION_KEYS: [&str; 4] = [
    VERSION_KEY,
    PUBLISH_TIME_KEY,
    ORIGIN_TIME_KEY,
    ORIGIN_ID_KEY,
];
pub const OBSERVATION_VERSION: i64 = 1;
pub const MAX_ORIGIN_ID_BYTES: usize = 256;

/// The top-level state of transport metadata before field lookup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MetadataContainer {
    /// Metadata is absent, including a top-level null in dynamic bindings.
    Missing,
    /// Metadata is a mapping whose fields can be queried by the lookup closure.
    Mapping,
    /// Metadata exists but is not a mapping.
    Invalid,
}

/// The only transport value kinds needed to parse observability metadata.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MetadataValueRef<'a> {
    Integer(i64),
    String(&'a str),
    /// The key exists with a type not accepted for that observability field.
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum MetadataIssue {
    MissingContext,
    InvalidMetadata,
    InvalidVersion,
    UnknownVersion,
    InvalidPublishTime,
    MissingOrigin,
    InvalidOriginTime,
    ConflictingOrigin,
    InvalidOriginId,
}

impl MetadataIssue {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::MissingContext => "missing_context",
            Self::InvalidMetadata => "invalid_metadata",
            Self::InvalidVersion => "invalid_version",
            Self::UnknownVersion => "unknown_version",
            Self::InvalidPublishTime => "invalid_publish_time",
            Self::MissingOrigin => "missing_origin",
            Self::InvalidOriginTime => "invalid_origin_time",
            Self::ConflictingOrigin => "conflicting_origin",
            Self::InvalidOriginId => "invalid_origin_id",
        }
    }

    const fn event_reason(self) -> EventReason {
        match self {
            Self::MissingContext => EventReason::MissingContext,
            Self::InvalidMetadata => EventReason::InvalidMetadata,
            Self::InvalidVersion => EventReason::InvalidVersion,
            Self::UnknownVersion => EventReason::UnknownVersion,
            Self::InvalidPublishTime => EventReason::InvalidPublishTime,
            Self::MissingOrigin => EventReason::MissingOrigin,
            Self::InvalidOriginTime => EventReason::InvalidOriginTime,
            Self::ConflictingOrigin => EventReason::ConflictingOrigin,
            Self::InvalidOriginId => EventReason::InvalidOriginId,
        }
    }
}

/// Validated fields detached from the caller's metadata map.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObservationMetadata {
    version: Option<i64>,
    publish_time_ns: Option<i64>,
    origin_time_ns: Option<i64>,
    origin_id: Option<Box<str>>,
    issues: Box<[MetadataIssue]>,
}

impl ObservationMetadata {
    pub const fn version(&self) -> Option<i64> {
        self.version
    }

    pub const fn publish_time_ns(&self) -> Option<i64> {
        self.publish_time_ns
    }

    pub const fn origin_time_ns(&self) -> Option<i64> {
        self.origin_time_ns
    }

    pub fn origin_id(&self) -> Option<&str> {
        self.origin_id.as_deref()
    }

    pub fn issues(&self) -> &[MetadataIssue] {
        &self.issues
    }

    pub fn is_unknown_version(&self) -> bool {
        matches!(self.version, Some(version) if version > 0 && version != OBSERVATION_VERSION)
    }
}

/// Parse v1 fields using typed lookups supplied by a transport adapter.
///
/// The lookup closure is not called for missing or invalid containers. For an
/// unknown version, only [`VERSION_KEY`] is queried so future fields remain opaque.
pub fn parse_metadata<'a>(
    container: MetadataContainer,
    mut get: impl FnMut(&str) -> Option<MetadataValueRef<'a>>,
) -> ObservationMetadata {
    let issue = match container {
        MetadataContainer::Missing => Some(MetadataIssue::MissingContext),
        MetadataContainer::Invalid => Some(MetadataIssue::InvalidMetadata),
        MetadataContainer::Mapping => None,
    };
    if let Some(issue) = issue {
        return ObservationMetadata {
            version: None,
            publish_time_ns: None,
            origin_time_ns: None,
            origin_id: None,
            issues: Box::new([issue]),
        };
    }

    let version_value = get(VERSION_KEY);
    let version = match version_value {
        None => {
            let has_other_fields = [PUBLISH_TIME_KEY, ORIGIN_TIME_KEY, ORIGIN_ID_KEY]
                .into_iter()
                .any(|key| get(key).is_some());
            let issue = if has_other_fields {
                MetadataIssue::InvalidVersion
            } else {
                MetadataIssue::MissingContext
            };
            return ObservationMetadata {
                version: None,
                publish_time_ns: None,
                origin_time_ns: None,
                origin_id: None,
                issues: Box::new([issue]),
            };
        }
        Some(MetadataValueRef::Integer(version)) if version == OBSERVATION_VERSION => version,
        Some(MetadataValueRef::Integer(version)) if version > 0 => {
            return ObservationMetadata {
                version: Some(version),
                publish_time_ns: None,
                origin_time_ns: None,
                origin_id: None,
                issues: Box::new([MetadataIssue::UnknownVersion]),
            };
        }
        _ => {
            return ObservationMetadata {
                version: None,
                publish_time_ns: None,
                origin_time_ns: None,
                origin_id: None,
                issues: Box::new([MetadataIssue::InvalidVersion]),
            };
        }
    };

    let publish_value = get(PUBLISH_TIME_KEY);
    let origin_value = get(ORIGIN_TIME_KEY);
    let origin_id_value = get(ORIGIN_ID_KEY);
    let mut issues = Vec::with_capacity(3);
    let publish_time_ns = match publish_value {
        Some(MetadataValueRef::Integer(value)) if value >= 0 => Some(value),
        _ => {
            issues.push(MetadataIssue::InvalidPublishTime);
            None
        }
    };
    let mut origin_time_ns = match origin_value {
        None => {
            issues.push(MetadataIssue::MissingOrigin);
            None
        }
        Some(MetadataValueRef::Integer(value)) if value >= 0 => Some(value),
        _ => {
            issues.push(MetadataIssue::InvalidOriginTime);
            None
        }
    };
    if matches!((publish_time_ns, origin_time_ns), (Some(publish), Some(origin)) if origin > publish)
    {
        issues.push(MetadataIssue::ConflictingOrigin);
        origin_time_ns = None;
    }

    let origin_id = match origin_id_value {
        None => None,
        Some(MetadataValueRef::String(value))
            if origin_time_ns.is_some() && valid_origin_id(value) =>
        {
            Some(Box::from(value))
        }
        Some(_) => {
            issues.push(MetadataIssue::InvalidOriginId);
            None
        }
    };

    ObservationMetadata {
        version: Some(version),
        publish_time_ns,
        origin_time_ns,
        origin_id,
        issues: issues.into_boxed_slice(),
    }
}

fn valid_origin_id(value: &str) -> bool {
    !value.is_empty() && value.len() <= MAX_ORIGIN_ID_BYTES
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OriginId(Box<str>);

impl OriginId {
    pub fn new(value: impl Into<Box<str>>) -> Result<Self, OriginIdError> {
        let value = value.into();
        if value.is_empty() {
            return Err(OriginIdError::Empty);
        }
        if value.len() > MAX_ORIGIN_ID_BYTES {
            return Err(OriginIdError::TooLong {
                bytes: value.len(),
                max: MAX_ORIGIN_ID_BYTES,
            });
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OriginIdError {
    Empty,
    TooLong { bytes: usize, max: usize },
}

impl fmt::Display for OriginIdError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty => write!(formatter, "origin ID must not be empty"),
            Self::TooLong { bytes, max } => {
                write!(
                    formatter,
                    "origin ID has {bytes} UTF-8 bytes; maximum is {max}"
                )
            }
        }
    }
}

impl Error for OriginIdError {}

pub trait Clock {
    /// Unix epoch nanoseconds, or `None` when unavailable or outside int64.
    fn unix_time_ns(&self) -> Option<i64>;

    /// Nanoseconds in this clock instance's local monotonic domain.
    fn monotonic_time_ns(&self) -> Option<u64>;
}

#[derive(Debug)]
pub struct SystemClock {
    monotonic_origin: Instant,
}

impl SystemClock {
    pub fn new() -> Self {
        Self {
            monotonic_origin: Instant::now(),
        }
    }
}

impl Default for SystemClock {
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for SystemClock {
    fn unix_time_ns(&self) -> Option<i64> {
        let elapsed = SystemTime::now().duration_since(UNIX_EPOCH).ok()?;
        i64::try_from(elapsed.as_nanos()).ok()
    }

    fn monotonic_time_ns(&self) -> Option<u64> {
        u64::try_from(self.monotonic_origin.elapsed().as_nanos())
            .ok()
            .filter(|value| *value <= MAX_VALUE_NS)
    }
}

/// Local receive state that can be cloned and cached with the actual data.
#[derive(Debug, Clone)]
pub struct ReceiveContext {
    metadata: ObservationMetadata,
    received_time_ns: Option<i64>,
    received_monotonic_ns: Option<u64>,
    input_id: Box<str>,
    domain: Arc<()>,
    process_id: u32,
}

impl ReceiveContext {
    pub fn metadata(&self) -> &ObservationMetadata {
        &self.metadata
    }

    pub const fn received_time_ns(&self) -> Option<i64> {
        self.received_time_ns
    }

    pub fn input_id(&self) -> &str {
        &self.input_id
    }
}

pub enum PublicationMode<'a> {
    NewOrigin { origin_id: Option<&'a OriginId> },
    Derived { parent: &'a ReceiveContext },
    Unlinked,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct V1PublicationFields {
    publish_time_ns: i64,
    origin_time_ns: Option<i64>,
    origin_id: Option<Box<str>>,
}

impl V1PublicationFields {
    pub const fn publish_time_ns(&self) -> i64 {
        self.publish_time_ns
    }

    pub const fn origin_time_ns(&self) -> Option<i64> {
        self.origin_time_ns
    }

    pub fn origin_id(&self) -> Option<&str> {
        self.origin_id.as_deref()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PublicationFields {
    /// Leave all Forge observability fields unchanged.
    PreserveOpaque,
    /// Remove all known Forge observability fields and insert none.
    Clear,
    /// Remove all known fields and insert these v1 values.
    V1(V1PublicationFields),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PublicationError {
    NewOriginInOpaqueMetadata,
}

impl fmt::Display for PublicationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NewOriginInOpaqueMetadata => {
                write!(
                    formatter,
                    "cannot create a v1 origin in unknown-version metadata"
                )
            }
        }
    }
}

impl Error for PublicationError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Turnaround {
    None,
    Duration(u64),
    Negative,
    InvalidParent,
}

/// Publication state prepared before the transport send and completed afterwards.
#[must_use = "call Observer::finish_publication with the transport send result"]
pub struct PreparedPublication {
    fields: PublicationFields,
    output_id: Box<str>,
    parent_input_id: Option<Box<str>>,
    turnaround: Turnaround,
}

impl PreparedPublication {
    pub fn fields(&self) -> &PublicationFields {
        &self.fields
    }
}

pub struct Observer<C = SystemClock> {
    clock: C,
    domain: Arc<()>,
    metrics: Option<Arc<BoundedMetricsSink>>,
}

impl Observer<SystemClock> {
    pub fn new(metrics: Option<Arc<BoundedMetricsSink>>) -> Self {
        Self::with_clock(SystemClock::new(), metrics)
    }
}

impl Default for Observer<SystemClock> {
    fn default() -> Self {
        Self::new(None)
    }
}

impl<C: Clock> Observer<C> {
    pub fn with_clock(clock: C, metrics: Option<Arc<BoundedMetricsSink>>) -> Self {
        Self {
            clock,
            domain: Arc::new(()),
            metrics,
        }
    }

    pub fn metrics(&self) -> Option<&Arc<BoundedMetricsSink>> {
        self.metrics.as_ref()
    }

    /// Observe one input immediately after the transport yields it to the app.
    pub fn observe_receive<'a>(
        &self,
        input_id: &str,
        container: MetadataContainer,
        get: impl FnMut(&str) -> Option<MetadataValueRef<'a>>,
    ) -> ReceiveContext {
        let (wall, monotonic) = self.sample_clocks();
        let metadata = parse_metadata(container, get);
        let labels = MetricLabels {
            input_id,
            output_id: "",
        };
        self.increment(EventReason::Received, labels);
        for issue in metadata.issues() {
            self.increment(issue.event_reason(), labels);
        }
        self.observe_wall_duration(
            LatencyMetric::Hop,
            EventReason::NegativeHop,
            wall,
            metadata.publish_time_ns(),
            labels,
        );
        self.observe_wall_duration(
            LatencyMetric::EndToEnd,
            EventReason::NegativeEndToEnd,
            wall,
            metadata.origin_time_ns(),
            labels,
        );
        ReceiveContext {
            metadata,
            received_time_ns: wall,
            received_monotonic_ns: monotonic,
            input_id: Box::from(input_id),
            domain: Arc::clone(&self.domain),
            process_id: process::id(),
        }
    }

    /// Prepare fields and local turnaround state before calling the transport.
    pub fn prepare_publication(
        &self,
        output_id: &str,
        mode: PublicationMode<'_>,
        outgoing_metadata: Option<&ObservationMetadata>,
    ) -> Result<PreparedPublication, PublicationError> {
        let outgoing_unknown = outgoing_metadata.is_some_and(|value| value.is_unknown_version());
        if outgoing_unknown && matches!(mode, PublicationMode::NewOrigin { .. }) {
            return Err(PublicationError::NewOriginInOpaqueMetadata);
        }

        let (wall, monotonic) = self.sample_clocks();
        let parent_unknown = matches!(mode, PublicationMode::Derived { parent } if parent.metadata.is_unknown_version());
        if outgoing_unknown || parent_unknown {
            self.increment(
                EventReason::UnknownVersion,
                MetricLabels {
                    input_id: "",
                    output_id,
                },
            );
        }

        let mut conflicting_origin = false;
        let fields = if outgoing_unknown {
            PublicationFields::PreserveOpaque
        } else if parent_unknown || wall.is_none() {
            PublicationFields::Clear
        } else {
            let publish_time_ns = wall.expect("wall is checked above");
            let (origin_time_ns, origin_id) = match mode {
                PublicationMode::NewOrigin { origin_id } => (
                    Some(publish_time_ns),
                    origin_id.map(|value| Box::from(value.as_str())),
                ),
                PublicationMode::Derived { parent } => match parent.metadata.origin_time_ns() {
                    Some(origin) if origin <= publish_time_ns => {
                        (Some(origin), parent.metadata.origin_id().map(Box::from))
                    }
                    Some(_) => {
                        conflicting_origin = true;
                        (None, None)
                    }
                    None => (None, None),
                },
                PublicationMode::Unlinked => (None, None),
            };
            PublicationFields::V1(V1PublicationFields {
                publish_time_ns,
                origin_time_ns,
                origin_id,
            })
        };

        if conflicting_origin {
            self.increment(
                EventReason::ConflictingOrigin,
                MetricLabels {
                    input_id: "",
                    output_id,
                },
            );
        }

        let (parent_input_id, turnaround) = match mode {
            PublicationMode::Derived { parent } => {
                let input_id = Some(parent.input_id.clone());
                if !Arc::ptr_eq(&self.domain, &parent.domain) || process::id() != parent.process_id
                {
                    (input_id, Turnaround::InvalidParent)
                } else {
                    let turnaround = match (monotonic, parent.received_monotonic_ns) {
                        (Some(end), Some(start)) if end >= start => {
                            Turnaround::Duration(end - start)
                        }
                        (Some(_), Some(_)) => Turnaround::Negative,
                        _ => Turnaround::None,
                    };
                    (input_id, turnaround)
                }
            }
            PublicationMode::NewOrigin { .. } | PublicationMode::Unlinked => {
                (None, Turnaround::None)
            }
        };

        Ok(PreparedPublication {
            fields,
            output_id: Box::from(output_id),
            parent_input_id,
            turnaround,
        })
    }

    /// Record send success/failure without changing the transport result.
    pub fn finish_publication<T, E>(
        &self,
        prepared: PreparedPublication,
        result: Result<T, E>,
    ) -> Result<T, E> {
        let output_id = prepared.output_id.as_ref();
        match result {
            Err(error) => {
                self.increment(
                    EventReason::PublishFailed,
                    MetricLabels {
                        input_id: "",
                        output_id,
                    },
                );
                Err(error)
            }
            Ok(value) => {
                self.increment(
                    EventReason::Published,
                    MetricLabels {
                        input_id: "",
                        output_id,
                    },
                );
                let input_id = prepared.parent_input_id.as_deref().unwrap_or("");
                let labels = MetricLabels {
                    input_id,
                    output_id,
                };
                match prepared.turnaround {
                    Turnaround::None => {}
                    Turnaround::Duration(duration) => {
                        self.observe(LatencyMetric::NodeTurnaround, duration, labels)
                    }
                    Turnaround::Negative => self.increment(EventReason::NegativeTurnaround, labels),
                    Turnaround::InvalidParent => self.increment(
                        EventReason::InvalidParent,
                        MetricLabels {
                            input_id: "",
                            output_id,
                        },
                    ),
                }
                Ok(value)
            }
        }
    }

    fn sample_clocks(&self) -> (Option<i64>, Option<u64>) {
        let wall = self.clock.unix_time_ns().filter(|value| *value >= 0);
        let monotonic = self
            .clock
            .monotonic_time_ns()
            .filter(|value| *value <= MAX_VALUE_NS);
        if wall.is_none() || monotonic.is_none() {
            self.increment(EventReason::ClockError, MetricLabels::default());
        }
        (wall, monotonic)
    }

    fn observe_wall_duration(
        &self,
        metric: LatencyMetric,
        negative_reason: EventReason,
        end: Option<i64>,
        start: Option<i64>,
        labels: MetricLabels<'_>,
    ) {
        let (Some(end), Some(start)) = (end, start) else {
            return;
        };
        if end < start {
            self.increment(negative_reason, labels);
        } else {
            self.observe(metric, (end - start) as u64, labels);
        }
    }

    fn observe(&self, metric: LatencyMetric, value_ns: u64, labels: MetricLabels<'_>) {
        if let Some(metrics) = &self.metrics {
            metrics.observe(metric, value_ns, labels);
        }
    }

    fn increment(&self, reason: EventReason, labels: MetricLabels<'_>) {
        if let Some(metrics) = &self.metrics {
            metrics.increment(reason, labels);
        }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};

    use super::*;
    use crate::metrics::{MetricsConfig, MetricsSnapshot};

    #[derive(Debug, Clone, PartialEq)]
    enum Value {
        Integer(i64),
        String(String),
        Bool,
        Float,
    }

    fn parse(values: &BTreeMap<&str, Value>) -> ObservationMetadata {
        parse_metadata(MetadataContainer::Mapping, |key| {
            values.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        })
    }

    fn values(entries: &[(&'static str, Value)]) -> BTreeMap<&'static str, Value> {
        entries.iter().cloned().collect()
    }

    #[test]
    fn parser_handles_complete_and_hop_only_contexts() {
        let complete = parse(&values(&[
            (VERSION_KEY, Value::Integer(1)),
            (PUBLISH_TIME_KEY, Value::Integer(100)),
            (ORIGIN_TIME_KEY, Value::Integer(20)),
            (ORIGIN_ID_KEY, Value::String("source/1".into())),
        ]));
        assert_eq!(complete.version(), Some(1));
        assert_eq!(complete.publish_time_ns(), Some(100));
        assert_eq!(complete.origin_time_ns(), Some(20));
        assert_eq!(complete.origin_id(), Some("source/1"));
        assert!(complete.issues().is_empty());

        let hop_only = parse(&values(&[
            (VERSION_KEY, Value::Integer(1)),
            (PUBLISH_TIME_KEY, Value::Integer(100)),
        ]));
        assert_eq!(hop_only.publish_time_ns(), Some(100));
        assert_eq!(hop_only.origin_time_ns(), None);
        assert_eq!(hop_only.issues(), &[MetadataIssue::MissingOrigin]);
    }

    #[test]
    fn parser_stops_on_missing_invalid_and_unknown_versions() {
        let missing = parse(&BTreeMap::new());
        assert_eq!(missing.issues(), &[MetadataIssue::MissingContext]);

        let missing = parse_metadata(MetadataContainer::Missing, |_| {
            panic!("missing metadata must not perform field lookup")
        });
        assert_eq!(missing.issues(), &[MetadataIssue::MissingContext]);

        let invalid = parse_metadata(MetadataContainer::Invalid, |_| {
            panic!("invalid metadata must not perform field lookup")
        });
        assert_eq!(invalid.issues(), &[MetadataIssue::InvalidMetadata]);

        for invalid in [
            Value::Integer(0),
            Value::Integer(-1),
            Value::Bool,
            Value::Float,
        ] {
            let parsed = parse(&values(&[(VERSION_KEY, invalid)]));
            assert_eq!(parsed.version(), None);
            assert_eq!(parsed.issues(), &[MetadataIssue::InvalidVersion]);
        }
        let missing_version = parse(&values(&[(PUBLISH_TIME_KEY, Value::Integer(100))]));
        assert_eq!(missing_version.issues(), &[MetadataIssue::InvalidVersion]);

        let mut requested = Vec::new();
        let unknown = parse_metadata(MetadataContainer::Mapping, |key| {
            requested.push(key.to_owned());
            if key == VERSION_KEY {
                Some(MetadataValueRef::Integer(2))
            } else {
                panic!("unknown metadata fields must remain opaque")
            }
        });
        assert_eq!(unknown.version(), Some(2));
        assert!(unknown.is_unknown_version());
        assert_eq!(unknown.publish_time_ns(), None);
        assert_eq!(unknown.issues(), &[MetadataIssue::UnknownVersion]);
        assert_eq!(requested, [VERSION_KEY]);
    }

    #[test]
    fn parser_keeps_independently_valid_fields_and_orders_issues() {
        let parsed = parse(&values(&[
            (VERSION_KEY, Value::Integer(1)),
            (PUBLISH_TIME_KEY, Value::Bool),
            (ORIGIN_TIME_KEY, Value::Integer(5)),
            (ORIGIN_ID_KEY, Value::String("id".into())),
        ]));
        assert_eq!(parsed.publish_time_ns(), None);
        assert_eq!(parsed.origin_time_ns(), Some(5));
        assert_eq!(parsed.origin_id(), Some("id"));
        assert_eq!(parsed.issues(), &[MetadataIssue::InvalidPublishTime]);

        let conflicting = parse(&values(&[
            (VERSION_KEY, Value::Integer(1)),
            (PUBLISH_TIME_KEY, Value::Integer(10)),
            (ORIGIN_TIME_KEY, Value::Integer(11)),
            (ORIGIN_ID_KEY, Value::String("id".into())),
        ]));
        assert_eq!(conflicting.publish_time_ns(), Some(10));
        assert_eq!(conflicting.origin_time_ns(), None);
        assert_eq!(conflicting.origin_id(), None);
        assert_eq!(
            conflicting.issues(),
            &[
                MetadataIssue::ConflictingOrigin,
                MetadataIssue::InvalidOriginId
            ]
        );
    }

    #[test]
    fn parser_rejects_wrong_types_negative_times_and_invalid_ids() {
        let parsed = parse(&values(&[
            (VERSION_KEY, Value::Integer(1)),
            (PUBLISH_TIME_KEY, Value::String("100".into())),
            (ORIGIN_TIME_KEY, Value::Integer(-1)),
            (ORIGIN_ID_KEY, Value::Integer(1)),
        ]));
        assert_eq!(
            parsed.issues(),
            &[
                MetadataIssue::InvalidPublishTime,
                MetadataIssue::InvalidOriginTime,
                MetadataIssue::InvalidOriginId,
            ]
        );

        let too_long = "界".repeat(86);
        let parsed = parse(&values(&[
            (VERSION_KEY, Value::Integer(1)),
            (PUBLISH_TIME_KEY, Value::Integer(1)),
            (ORIGIN_TIME_KEY, Value::Integer(1)),
            (ORIGIN_ID_KEY, Value::String(too_long)),
        ]));
        assert_eq!(parsed.issues(), &[MetadataIssue::InvalidOriginId]);
    }

    #[test]
    fn origin_id_validates_utf8_bytes() {
        assert_eq!(OriginId::new(""), Err(OriginIdError::Empty));
        assert!(OriginId::new("x".repeat(256)).is_ok());
        assert_eq!(
            OriginId::new("界".repeat(86)),
            Err(OriginIdError::TooLong {
                bytes: 258,
                max: 256
            })
        );
    }

    struct FakeClock {
        wall: AtomicI64,
        monotonic: AtomicU64,
    }

    impl FakeClock {
        fn new(wall: i64, monotonic: u64) -> Self {
            Self {
                wall: AtomicI64::new(wall),
                monotonic: AtomicU64::new(monotonic),
            }
        }

        fn set(&self, wall: i64, monotonic: u64) {
            self.wall.store(wall, Ordering::Relaxed);
            self.monotonic.store(monotonic, Ordering::Relaxed);
        }
    }

    impl Clock for FakeClock {
        fn unix_time_ns(&self) -> Option<i64> {
            Some(self.wall.load(Ordering::Relaxed))
        }

        fn monotonic_time_ns(&self) -> Option<u64> {
            Some(self.monotonic.load(Ordering::Relaxed))
        }
    }

    struct MissingClock;

    impl Clock for MissingClock {
        fn unix_time_ns(&self) -> Option<i64> {
            None
        }

        fn monotonic_time_ns(&self) -> Option<u64> {
            None
        }
    }

    fn sink() -> Arc<BoundedMetricsSink> {
        Arc::new(BoundedMetricsSink::new(MetricsConfig::default()).unwrap())
    }

    fn counter(snapshot: &MetricsSnapshot, reason: EventReason) -> u64 {
        snapshot
            .counters
            .iter()
            .filter(|row| row.reason == reason)
            .map(|row| row.count)
            .sum()
    }

    fn histogram(snapshot: &MetricsSnapshot, metric: LatencyMetric) -> Vec<u64> {
        snapshot
            .histograms
            .iter()
            .filter(|row| row.metric == metric)
            .map(|row| row.sum_ns as u64)
            .collect()
    }

    fn complete() -> BTreeMap<&'static str, Value> {
        values(&[
            (VERSION_KEY, Value::Integer(1)),
            (PUBLISH_TIME_KEY, Value::Integer(100)),
            (ORIGIN_TIME_KEY, Value::Integer(20)),
            (ORIGIN_ID_KEY, Value::String("source".into())),
        ])
    }

    #[test]
    fn receive_records_exact_hop_e2e_and_issues() {
        let metrics = sink();
        let observer = Observer::with_clock(FakeClock::new(150, 1_000), Some(metrics.clone()));
        let input = complete();
        let context = observer.observe_receive("image", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        assert_eq!(context.received_time_ns(), Some(150));
        assert_eq!(context.input_id(), "image");
        let snapshot = metrics.snapshot();
        assert_eq!(histogram(&snapshot, LatencyMetric::Hop), vec![50]);
        assert_eq!(histogram(&snapshot, LatencyMetric::EndToEnd), vec![130]);
        assert_eq!(counter(&snapshot, EventReason::Received), 1);
    }

    #[test]
    fn new_unlinked_and_derived_fields_follow_protocol() {
        let metrics = sink();
        let clock = FakeClock::new(150, 1_000);
        let observer = Observer::with_clock(clock, Some(metrics.clone()));
        let origin_id = OriginId::new("camera/1").unwrap();
        let new = observer
            .prepare_publication(
                "image",
                PublicationMode::NewOrigin {
                    origin_id: Some(&origin_id),
                },
                None,
            )
            .unwrap();
        let PublicationFields::V1(fields) = new.fields() else {
            panic!("expected v1")
        };
        assert_eq!(fields.publish_time_ns(), 150);
        assert_eq!(fields.origin_time_ns(), Some(150));
        assert_eq!(fields.origin_id(), Some("camera/1"));
        observer.finish_publication(new, Ok::<_, ()>(())).unwrap();

        observer.clock.set(160, 1_010);
        let unlinked = observer
            .prepare_publication("image", PublicationMode::Unlinked, None)
            .unwrap();
        let PublicationFields::V1(fields) = unlinked.fields() else {
            panic!("expected v1")
        };
        assert_eq!(fields.publish_time_ns(), 160);
        assert_eq!(fields.origin_time_ns(), None);

        let input = complete();
        let parent = observer.observe_receive("image", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        observer.clock.set(200, 1_110);
        let derived = observer
            .prepare_publication("out", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        let PublicationFields::V1(fields) = derived.fields() else {
            panic!("expected v1")
        };
        assert_eq!(fields.publish_time_ns(), 200);
        assert_eq!(fields.origin_time_ns(), Some(20));
        assert_eq!(fields.origin_id(), Some("source"));
        observer
            .finish_publication(derived, Ok::<_, ()>(()))
            .unwrap();

        let snapshot = metrics.snapshot();
        assert_eq!(counter(&snapshot, EventReason::Published), 2);
        assert_eq!(
            histogram(&snapshot, LatencyMetric::NodeTurnaround),
            vec![100]
        );
    }

    #[test]
    fn send_failure_is_returned_unchanged_and_not_counted_as_success() {
        let metrics = sink();
        let observer = Observer::with_clock(FakeClock::new(10, 10), Some(metrics.clone()));
        let prepared = observer
            .prepare_publication("out", PublicationMode::Unlinked, None)
            .unwrap();
        let error = String::from("send failed");
        let returned = observer
            .finish_publication::<(), _>(prepared, Err(error.clone()))
            .unwrap_err();
        assert_eq!(returned, error);
        let snapshot = metrics.snapshot();
        assert_eq!(counter(&snapshot, EventReason::PublishFailed), 1);
        assert_eq!(counter(&snapshot, EventReason::Published), 0);
        assert!(histogram(&snapshot, LatencyMetric::NodeTurnaround).is_empty());
    }

    #[test]
    fn foreign_observer_propagates_origin_without_turnaround() {
        let input = complete();
        let first = Observer::with_clock(FakeClock::new(150, 1_000), None);
        let parent = first.observe_receive("image", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        let metrics = sink();
        let second = Observer::with_clock(FakeClock::new(200, 1_100), Some(metrics.clone()));
        let prepared = second
            .prepare_publication("out", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        let PublicationFields::V1(fields) = prepared.fields() else {
            panic!("expected v1")
        };
        assert_eq!(fields.origin_time_ns(), Some(20));
        second
            .finish_publication(prepared, Ok::<_, ()>(()))
            .unwrap();
        let snapshot = metrics.snapshot();
        assert_eq!(counter(&snapshot, EventReason::InvalidParent), 1);
        assert!(histogram(&snapshot, LatencyMetric::NodeTurnaround).is_empty());
    }

    #[test]
    fn clock_rollback_drops_origin_and_negative_receive_is_anomaly() {
        let metrics = sink();
        let observer = Observer::with_clock(FakeClock::new(10, 1_000), Some(metrics.clone()));
        let input = complete();
        let parent = observer.observe_receive("image", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        let prepared = observer
            .prepare_publication("out", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        let PublicationFields::V1(fields) = prepared.fields() else {
            panic!("expected v1")
        };
        assert_eq!(fields.origin_time_ns(), None);
        let snapshot = metrics.snapshot();
        assert_eq!(counter(&snapshot, EventReason::NegativeHop), 1);
        assert_eq!(counter(&snapshot, EventReason::NegativeEndToEnd), 1);
        assert_eq!(counter(&snapshot, EventReason::ConflictingOrigin), 1);
        assert!(histogram(&snapshot, LatencyMetric::Hop).is_empty());
    }

    #[test]
    fn unknown_versions_are_opaque_and_cannot_become_new_origin() {
        let unknown = parse(&values(&[(VERSION_KEY, Value::Integer(2))]));
        let metrics = sink();
        let observer = Observer::with_clock(FakeClock::new(100, 100), Some(metrics.clone()));
        let prepared = observer
            .prepare_publication("out", PublicationMode::Unlinked, Some(&unknown))
            .unwrap();
        assert_eq!(prepared.fields(), &PublicationFields::PreserveOpaque);
        assert!(matches!(
            observer.prepare_publication(
                "out",
                PublicationMode::NewOrigin { origin_id: None },
                Some(&unknown)
            ),
            Err(PublicationError::NewOriginInOpaqueMetadata)
        ));

        let parent = observer.observe_receive("in", MetadataContainer::Mapping, |key| {
            if key == VERSION_KEY {
                Some(MetadataValueRef::Integer(2))
            } else {
                None
            }
        });
        let prepared = observer
            .prepare_publication("out", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        assert_eq!(prepared.fields(), &PublicationFields::Clear);
    }

    #[test]
    fn unavailable_clocks_clear_fields_without_blocking_completion() {
        let metrics = sink();
        let observer = Observer::with_clock(MissingClock, Some(metrics.clone()));
        let prepared = observer
            .prepare_publication("out", PublicationMode::Unlinked, None)
            .unwrap();
        assert_eq!(prepared.fields(), &PublicationFields::Clear);
        assert_eq!(observer.finish_publication(prepared, Ok::<_, ()>(7)), Ok(7));
        assert_eq!(counter(&metrics.snapshot(), EventReason::ClockError), 1);
    }

    #[test]
    fn wall_and_monotonic_clock_failures_are_isolated() {
        let input = complete();
        let metrics = sink();
        let observer = Observer::with_clock(FakeClock::new(-1, 100), Some(metrics.clone()));
        let parent = observer.observe_receive("in", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        observer.clock.set(-1, 150);
        let prepared = observer
            .prepare_publication("out", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        assert_eq!(prepared.fields(), &PublicationFields::Clear);
        observer
            .finish_publication(prepared, Ok::<_, ()>(()))
            .unwrap();
        let snapshot = metrics.snapshot();
        assert_eq!(counter(&snapshot, EventReason::ClockError), 2);
        assert_eq!(
            histogram(&snapshot, LatencyMetric::NodeTurnaround),
            vec![50]
        );
        assert!(histogram(&snapshot, LatencyMetric::Hop).is_empty());

        let metrics = sink();
        let observer =
            Observer::with_clock(FakeClock::new(150, MAX_VALUE_NS + 1), Some(metrics.clone()));
        let parent = observer.observe_receive("in", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        observer.clock.set(200, MAX_VALUE_NS + 1);
        let prepared = observer
            .prepare_publication("out", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        let PublicationFields::V1(fields) = prepared.fields() else {
            panic!("expected v1")
        };
        assert_eq!(fields.publish_time_ns(), 200);
        assert_eq!(fields.origin_time_ns(), Some(20));
        observer
            .finish_publication(prepared, Ok::<_, ()>(()))
            .unwrap();
        let snapshot = metrics.snapshot();
        assert_eq!(counter(&snapshot, EventReason::ClockError), 2);
        assert_eq!(histogram(&snapshot, LatencyMetric::Hop), vec![50]);
        assert!(histogram(&snapshot, LatencyMetric::NodeTurnaround).is_empty());
    }

    #[test]
    fn monotonic_rollback_is_an_anomaly_not_a_zero_sample() {
        let input = complete();
        let metrics = sink();
        let observer = Observer::with_clock(FakeClock::new(150, 100), Some(metrics.clone()));
        let parent = observer.observe_receive("in", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        observer.clock.set(200, 90);
        let prepared = observer
            .prepare_publication("out", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        observer
            .finish_publication(prepared, Ok::<_, ()>(()))
            .unwrap();

        let snapshot = metrics.snapshot();
        assert_eq!(counter(&snapshot, EventReason::NegativeTurnaround), 1);
        assert!(histogram(&snapshot, LatencyMetric::NodeTurnaround).is_empty());
    }

    #[test]
    fn cloned_context_supports_fan_out_with_independent_preparations() {
        let input = complete();
        let clock = FakeClock::new(150, 1_000);
        let observer = Observer::with_clock(clock, None);
        let parent = observer.observe_receive("image", MetadataContainer::Mapping, |key| {
            input.get(key).map(|value| match value {
                Value::Integer(value) => MetadataValueRef::Integer(*value),
                Value::String(value) => MetadataValueRef::String(value),
                Value::Bool | Value::Float => MetadataValueRef::Other,
            })
        });
        let cloned = parent.clone();
        observer.clock.set(200, 1_100);
        let left = observer
            .prepare_publication("left", PublicationMode::Derived { parent: &parent }, None)
            .unwrap();
        observer.clock.set(210, 1_110);
        let right = observer
            .prepare_publication("right", PublicationMode::Derived { parent: &cloned }, None)
            .unwrap();
        let PublicationFields::V1(left) = left.fields() else {
            panic!("expected v1")
        };
        let PublicationFields::V1(right) = right.fields() else {
            panic!("expected v1")
        };
        assert_eq!(left.origin_time_ns(), Some(20));
        assert_eq!(right.origin_time_ns(), Some(20));
        assert_eq!(left.publish_time_ns(), 200);
        assert_eq!(right.publish_time_ns(), 210);
    }

    #[test]
    fn system_clock_produces_supported_values() {
        let clock = SystemClock::new();
        assert!(clock.unix_time_ns().is_some_and(|value| value > 0));
        assert!(clock.monotonic_time_ns().is_some());
    }
}
