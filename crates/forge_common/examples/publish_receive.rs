//! Minimal new-origin -> receive -> derived-publish observability example.

use std::collections::BTreeMap;
use std::convert::Infallible;
use std::error::Error;
use std::sync::Arc;

use forge_common::metrics::{BoundedMetricsSink, MetricsConfig};
use forge_common::observability::{
    MetadataContainer, MetadataValueRef, OBSERVATION_KEYS, OBSERVATION_VERSION, ORIGIN_ID_KEY,
    ORIGIN_TIME_KEY, Observer, OriginId, PUBLISH_TIME_KEY, PublicationFields, PublicationMode,
    VERSION_KEY, parse_metadata,
};

#[derive(Debug, Clone)]
enum Parameter {
    Integer(i64),
    String(String),
    Other,
}

fn metadata_value(value: Option<&Parameter>) -> Option<MetadataValueRef<'_>> {
    value.map(|value| match value {
        Parameter::Integer(value) => MetadataValueRef::Integer(*value),
        Parameter::String(value) => MetadataValueRef::String(value),
        Parameter::Other => MetadataValueRef::Other,
    })
}

fn clear_observation_fields(parameters: &mut BTreeMap<String, Parameter>) {
    for key in OBSERVATION_KEYS {
        parameters.remove(key);
    }
}

fn apply_publication_fields(
    parameters: &mut BTreeMap<String, Parameter>,
    fields: &PublicationFields,
) {
    match fields {
        PublicationFields::PreserveOpaque => {}
        PublicationFields::Clear => clear_observation_fields(parameters),
        PublicationFields::V1(fields) => {
            clear_observation_fields(parameters);
            parameters.insert(VERSION_KEY.into(), Parameter::Integer(OBSERVATION_VERSION));
            parameters.insert(
                PUBLISH_TIME_KEY.into(),
                Parameter::Integer(fields.publish_time_ns()),
            );
            if let Some(origin_time_ns) = fields.origin_time_ns() {
                parameters.insert(ORIGIN_TIME_KEY.into(), Parameter::Integer(origin_time_ns));
            }
            if let Some(origin_id) = fields.origin_id() {
                parameters.insert(ORIGIN_ID_KEY.into(), Parameter::String(origin_id.into()));
            }
        }
    }
}

fn send_output(
    parameters: BTreeMap<String, Parameter>,
) -> Result<BTreeMap<String, Parameter>, Infallible> {
    Ok(parameters)
}

fn main() -> Result<(), Box<dyn Error>> {
    let camera = Observer::default();
    let origin_id = OriginId::new("usb-camera/image")?;
    let mut camera_parameters = BTreeMap::from([
        (
            "capture_timestamp_ns".into(),
            Parameter::Integer(1_700_000_000_000_000_000),
        ),
        ("business_flag".into(), Parameter::Other),
    ]);

    let prepared = camera.prepare_publication(
        "image",
        PublicationMode::NewOrigin {
            origin_id: Some(&origin_id),
        },
        None,
    )?;
    apply_publication_fields(&mut camera_parameters, prepared.fields());
    let camera_parameters = camera.finish_publication(prepared, send_output(camera_parameters))?;

    let sink = Arc::new(BoundedMetricsSink::new(MetricsConfig::default())?);
    let viewer = Observer::new(Some(Arc::clone(&sink)));
    let context = viewer.observe_receive("image", MetadataContainer::Mapping, |key| {
        metadata_value(camera_parameters.get(key))
    });

    let mut forwarded_parameters = camera_parameters.clone();
    let outgoing_observation = parse_metadata(MetadataContainer::Mapping, |key| {
        metadata_value(forwarded_parameters.get(key))
    });
    let prepared = viewer.prepare_publication(
        "forwarded_image",
        PublicationMode::Derived { parent: &context },
        Some(&outgoing_observation),
    )?;
    apply_publication_fields(&mut forwarded_parameters, prepared.fields());
    let _forwarded_parameters =
        viewer.finish_publication(prepared, send_output(forwarded_parameters))?;

    let interval = sink.take_snapshot();
    for histogram in interval.histograms {
        println!(
            "{} input={} output={} count={} sum_ns={}",
            histogram.metric.as_str(),
            histogram.input_id,
            histogram.output_id,
            histogram.count,
            histogram.sum_ns
        );
    }
    for counter in interval.counters {
        println!(
            "forge_observability_events_total reason={} input={} output={} count={}",
            counter.reason.as_str(),
            counter.input_id,
            counter.output_id,
            counter.count
        );
    }

    Ok(())
}
