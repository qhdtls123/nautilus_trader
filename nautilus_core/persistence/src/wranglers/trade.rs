// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2023 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

use std::{collections::HashMap, io::Cursor, str::FromStr};

use datafusion::arrow::ipc::reader::StreamReader;
use nautilus_model::{data::trade::TradeTick, identifiers::instrument_id::InstrumentId};
use pyo3::{exceptions::PyValueError, prelude::*};

use crate::arrow::DecodeFromRecordBatch;

#[pyclass]
pub struct TradeTickDataWrangler {
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    metadata: HashMap<String, String>,
}

#[pymethods]
impl TradeTickDataWrangler {
    #[new]
    fn py_new(instrument_id: &str, price_precision: u8, size_precision: u8) -> PyResult<Self> {
        let instrument_id = InstrumentId::from_str(instrument_id)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;

        let metadata = TradeTick::get_metadata(&instrument_id, price_precision, size_precision);

        Ok(Self {
            instrument_id,
            price_precision,
            size_precision,
            metadata,
        })
    }

    #[getter]
    fn instrument_id(&self) -> String {
        self.instrument_id.to_string()
    }

    #[getter]
    fn price_precision(&self) -> u8 {
        self.price_precision
    }

    #[getter]
    fn size_precision(&self) -> u8 {
        self.size_precision
    }

    fn process_record_batches_bytes(&self, _py: Python, data: &[u8]) -> PyResult<Vec<TradeTick>> {
        // Create a StreamReader (from Arrow IPC)
        let cursor = Cursor::new(data);
        let reader = match StreamReader::try_new(cursor, None) {
            Ok(reader) => reader,
            Err(e) => return Err(PyValueError::new_err(e.to_string())),
        };

        let mut ticks = Vec::new();

        // Read the record batches
        for maybe_batch in reader {
            let record_batch = match maybe_batch {
                Ok(record_batch) => record_batch,
                Err(e) => return Err(PyValueError::new_err(e.to_string())),
            };

            let batch_deltas = TradeTick::decode_batch(&self.metadata, record_batch);
            ticks.extend(batch_deltas);
        }

        Ok(ticks)
    }
}
